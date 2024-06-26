from CLAPWrapper import CLAPWrapper
import torch
import numpy as np
from copy import deepcopy
import torch.nn as nn
from esc50_dataset import *
from urban8k_dataset import *
import torch.nn.functional as F
import numpy as np
from tqdm import tqdm
from importlib_resources import files
from models.utils import read_config_as_args
from sklearn.metrics import accuracy_score
from torch.utils.data import Dataset, DataLoader
import math
import torch

import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"

def eval_one_epoch(eval_dataloader, wrapper, y):
    wrapper.clap.eval()
    wrapper.prompts_embeddings.eval()

    label_embeds, _ = wrapper.get_text_embeddings(y) 
    label_embeds_frame, _ = wrapper.get_text_embeddings(y, frame=True)    

    y_preds, y_preds_frame, y_preds_ensemble, y_labels = [], [], [], []
    for _, (audio, _, one_hot_target) in tqdm(enumerate(eval_dataloader), total=len(eval_dataloader)):
        audio = audio.to(device, non_blocking=True)
        with torch.no_grad():    
            logits = wrapper.compute_at_similarity(audio, label_embeds, is_spec_augment=False)
            y_pred = F.softmax(logits.detach().cpu(), dim=1).numpy()
        

        y_preds.append(y_pred)
        y_labels.append(one_hot_target.detach().cpu().numpy())

    y_labels, y_preds = np.concatenate(y_labels, axis=0), np.concatenate(y_preds, axis=0)
    
    acc = accuracy_score(np.argmax(y_labels, axis=1), np.argmax(y_preds, axis=1))
    print('Accuracy coarse-grained: {}'.format(acc))
    return acc

def train_one_epoch(train_dataloader, wrapper, optimizer, device, y):
    wrapper.clap.train()
    wrapper.frozen_caption_encoder.eval()
    wrapper.prompts_embeddings.train()

    y_preds, y_labels = [], []
    loss_func = torch.nn.CrossEntropyLoss()

    for _, (audio, _, one_hot_target) in tqdm(enumerate(train_dataloader), total=len(train_dataloader)):
        audio = audio.to(device, non_blocking=True)
        one_hot_targets = one_hot_target.to(device)
        _, targets = one_hot_targets.max(-1)
        label_embeds, _ = wrapper.get_text_embeddings(y)
        logits = wrapper.compute_at_similarity(audio, label_embeds, is_spec_augment=False)
        loss_st = loss_func(logits, targets) 
        optimizer.zero_grad()
        loss = loss_st
        loss.backward()     
        optimizer.step()

    return

config_as_str = files('configs').joinpath('config.yml').read_text()
args = read_config_as_args(config_as_str, is_config_str=True)
device = "cuda" if args.use_cuda else "cpu"

wrapper = CLAPWrapper(args)

if args.dataset == 'esc':
    #train_dataset = ESC_Text_fs(args.esc_text_path, support=16)
    train_dataset = ESC50(root=args.esc_audio_path, download=False)
    eval_dataset = ESC50(root=args.esc_audio_path, download=False)
else:
    train_dataset = URBAN8K_Text_fs(args.urbansound_text_path, support=128)
    eval_dataset = URBAN8K(root=args.urbansound_audio_path, download=False)

num_class = eval_dataset.class_num
train_dataloader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=8)
eval_dataloader = DataLoader(eval_dataset, batch_size=args.batch_size, shuffle=False, num_workers=8)

prompt = ' {}'
y = [prompt.format(x) for x in train_dataset.classes]
print('y:', y)
optimizer = torch.optim.AdamW([{'params': wrapper.prompts_embeddings.parameters()}], lr=args.lr, betas=(0.9, 0.98), eps=1e-06)


best_acc = 0
for e in range(args.epochs):
    print("Train Epoch {}: ".format(e))
    train_one_epoch(train_dataloader, wrapper, optimizer, device, y)
    print("Infer Epoch {}: ".format(e))
    acc = eval_one_epoch(eval_dataloader, wrapper, y)
    if acc > best_acc:
        best_acc = acc
        torch.save(wrapper.prompts_embeddings.state_dict(), 'best_model_coarse.pt')