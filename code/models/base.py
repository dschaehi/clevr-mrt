import torch
import os
import time
import numpy as np
import json
from tqdm import tqdm
from collections import OrderedDict
from torch import optim
from torch import nn
from torchvision.utils import save_image

class Base:
    def __init__(self):
        pass

    def prepare_batch(self, batch):
        if len(batch) != 7:
            raise Exception("Expected batch to only contain six elements: " +
                            "X_batch, X2_batch, q_batch, cam_batch, cam2_batch, y_batch, meta_batch")
        X_batch = batch[0].float()
        X2_batch = batch[1]

        if type(X2_batch) != list:
            X2_batch = X2_batch.float()
        else:
            # This happens when the dataset does
            # not support multiple camera views.
            X2_batch = None
        q_batch = batch[2]
        cam_batch = batch[3]
        cam2_batch = batch[4]
        y_batch = batch[5].flatten()
        meta_batch = batch[6]
        if self.use_cuda:
            X_batch = X_batch.to(self.device)
            if type(X2_batch) is not None:
                X2_batch = X2_batch.to(self.device)
            q_batch = q_batch.to(self.device)
            cam_batch = cam_batch.to(self.device)
            cam2_batch = cam2_batch.to(self.device)
            y_batch = y_batch.to(self.device)
        return [X_batch, X2_batch,
                q_batch, cam_batch, cam2_batch,
                y_batch, meta_batch]

    def fit(self,
            itr_train,
            itr_valid,
            epochs,
            model_dir,
            result_dir,
            save_every=1,
            scheduler_fn=None,
            scheduler_args={},
            verbose=True):
        for folder_name in [model_dir, result_dir]:
            if folder_name is not None and not os.path.exists(folder_name):
                os.makedirs(folder_name)
        f_mode = 'w' if not os.path.exists("%s/results.json" % result_dir) else 'a'
        f = None
        if result_dir is not None:
            f = open("%s/results.json" % result_dir, f_mode)
        for epoch in range(self.last_epoch, epochs):
            epoch_start_time = time.time()
            # Training.
            if verbose:
                pbar = tqdm(total=len(itr_train))
            train_dict = OrderedDict({'epoch': epoch+1})
            train_start_time = time.time()
            for b, batch in enumerate(itr_train):
                batch = self.prepare_batch(batch)
                total_iter = (len(itr_train)*epoch) + b
                losses, outputs = self.train_on_instance(*batch,
                                                         iter=b+1,
                                                         total_iter=total_iter,
                                                         epoch=epoch+1)
                for key in losses:
                    this_key = 'train_%s' % key
                    if this_key not in train_dict:
                        train_dict[this_key] = []
                    train_dict[this_key].append(losses[key])
                if verbose:
                    pbar.update(1)
                    pbar.set_postfix(self._get_stats(train_dict, 'train'))
                # Process handlers.
                for handler_fn in self.handlers:
                    handler_dict = handler_fn(losses, batch, outputs,
                                              {'epoch':epoch+1, 'iter':b+1, 'mode':'train'})
                    for key in handler_dict.keys():
                        this_key = 'train_%s' % key
                        if this_key not in train_dict:
                            train_dict[this_key] = []
                        train_dict[this_key].append(handler_dict[key])
            train_end_time = time.time()
            # Compute iters per second
            iter_per_sec = len(itr_train) / (train_end_time - train_start_time)
            iter_per_hr = iter_per_sec * 60 * 60
            if verbose:
                pbar.close()
            valid_dict = {}
            # TODO: enable valid
            if verbose:
                pbar = tqdm(total=len(itr_valid))
            # Validation.
            valid_dict = OrderedDict({})
            for b, valid_batch in enumerate(itr_valid):
                valid_batch = self.prepare_batch(valid_batch)
                total_iter = (len(itr_valid)*epoch) + b
                valid_losses, valid_outputs = self.eval_on_instance(*valid_batch,
                                                                    total_iter=total_iter,
                                                                    iter=b+1,
                                                                    epoch=epoch+1)
                for key in valid_losses:
                    this_key = 'valid_%s' % key
                    if this_key not in valid_dict:
                        valid_dict[this_key] = []
                    valid_dict[this_key].append(valid_losses[key])
                if verbose:
                    pbar.update(1)
                    pbar.set_postfix(self._get_stats(valid_dict, 'valid'))
                # Process handlers.
                for handler_fn in self.handlers:
                    handler_dict = handler_fn(valid_losses, valid_batch, valid_outputs,
                                              {'epoch':epoch+1, 'iter':b+1, 'mode':'valid'})
                    for key in handler_dict.keys():
                        this_key = 'valid_%s' % key
                        if this_key not in valid_dict:
                            valid_dict[this_key] = []
                        valid_dict[this_key].append(handler_dict[key])

            if verbose:
                pbar.close()
            # Step learning rates.
            for sched in self.schedulers:
                sched.step()
            # Update dictionary of values.
            all_dict = train_dict
            all_dict.update(valid_dict)
            for key in all_dict:
                all_dict[key] = np.mean(all_dict[key])
            for key in self.optim:
                all_dict["lr_%s" % key] = \
                        self.optim[key].state_dict()['param_groups'][0]['lr']
            time_per_epoch = time.time() - epoch_start_time
            all_dict['time'] = time_per_epoch
            all_dict['hmd_100'] = (time_per_epoch*100.0) / 60. / 60. / 24.
            all_dict['iter_per_hr'] = iter_per_hr
            all_dict['iters'] = len(itr_train)
            str_ = json.dumps(all_dict)
            print(str_)
            if result_dir is not None:
                f.write(str_ + "\n")
                f.flush()
            if (epoch+1) % save_every == 0 and model_dir is not None:
                self.save(filename="%s/%i.pkl" % (model_dir, epoch+1),
                          epoch=epoch+1)
        if f is not None:
            f.close()

    def _get_stats(self, dict_, mode, window_sz=100):
        stats = OrderedDict({})
        for key in dict_.keys():
            if key == 'epoch':
                stats[key] = dict_[key]
            else:
                stats[key] = np.mean(dict_[key][-window_sz:])
        return stats
