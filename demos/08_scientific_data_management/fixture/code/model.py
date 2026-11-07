"""GCMC MOF yield model — Blank Project code sample for catalog demo."""

import torch


class YieldPredictor(torch.nn.Module):
    """Predict CO2 uptake from MOF descriptors."""

    def __init__(self, input_dim: int = 12) -> None:
        super().__init__()
        self.net = torch.nn.Sequential(
            torch.nn.Linear(input_dim, 64),
            torch.nn.ReLU(),
            torch.nn.Linear(64, 1),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.net(features)
