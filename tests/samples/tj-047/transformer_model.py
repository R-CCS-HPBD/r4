# transformer_model.py — simplified transformer attention
import torch
import torch.nn as nn


class MultiHeadAttention(nn.Module):
    def __init__(self, d_model, num_heads):
        super().__init__()
        self.num_heads = num_heads
        self.d_model = d_model

    def forward(self, query, key, value, _mask=None):
        # Scaled dot-product attention
        scores = torch.matmul(query, key.transpose(-2, -1)) / (query.shape[-1] ** 0.5)
        attention = torch.softmax(scores, dim=-1)
        return torch.matmul(attention, value)


# Reference: Vaswani et al., 2017
