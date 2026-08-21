from __future__ import annotations

import hashlib

import numpy as np
import torch
from torch import nn

from .wire import encode_arrays


class FullMLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(36, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, 32)
        self.fc4 = nn.Linear(32, 12)

    def forward(self, x):
        return self.fc4(
            torch.relu(
                self.fc3(
                    torch.relu(
                        self.fc2(
                            torch.relu(self.fc1(x))
                        )
                    )
                )
            )
        )


class EarlyHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(36, 128)

    def forward(self, x):
        return torch.relu(self.fc1(x))


class EarlyMiddle(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, 32)

    def forward(self, x):
        return torch.relu(
            self.fc3(
                torch.relu(self.fc2(x))
            )
        )


class DeepHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(36, 128)
        self.fc2 = nn.Linear(128, 64)

    def forward(self, x):
        return torch.relu(
            self.fc2(
                torch.relu(self.fc1(x))
            )
        )


class DeepMiddle(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc3 = nn.Linear(64, 32)

    def forward(self, x):
        return torch.relu(self.fc3(x))


class Tail(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc4 = nn.Linear(32, 12)

    def forward(self, x):
        return self.fc4(x)


class ConventionalTail(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, 32)
        self.fc4 = nn.Linear(32, 12)

    def forward(self, x):
        return self.fc4(
            torch.relu(
                self.fc3(
                    torch.relu(self.fc2(x))
                )
            )
        )


def segments(configuration: str):
    if configuration in ("II-U-E", "II-U-H"):
        return EarlyHead(), EarlyMiddle(), Tail(), "early"

    if configuration == "II-U-D":
        return DeepHead(), DeepMiddle(), Tail(), "deep"

    if configuration == "II-S":
        return EarlyHead(), ConventionalTail(), None, "conventional"

    raise ValueError(configuration)


def arrays_from_state(module: nn.Module) -> dict[str, np.ndarray]:
    return {
        k: v.detach().cpu().numpy()
        for k, v in module.state_dict().items()
    }


def load_arrays(
    module: nn.Module,
    arrays: dict[str, np.ndarray],
):
    current = module.state_dict()
    selected = {
        k: torch.from_numpy(arrays[k]).to(dtype=current[k].dtype)
        for k in current
        if k in arrays
    }

    if set(selected) != set(current):
        raise ValueError(
            f"missing state keys {set(current) - set(selected)}"
        )

    module.load_state_dict(selected)


def combined_state(
    *modules: nn.Module,
) -> dict[str, np.ndarray]:
    out = {}

    for m in modules:
        if m is None:
            continue

        overlap = set(out) & set(m.state_dict())

        if overlap:
            raise ValueError(f"duplicate keys: {overlap}")

        out.update(arrays_from_state(m))

    if set(out) != set(FullMLP().state_dict()):
        raise ValueError("incomplete logical model")

    return out


def state_hash(arrays: dict[str, np.ndarray]) -> str:
    return hashlib.sha256(
        encode_arrays(arrays)
    ).hexdigest()


def average_states(
    weighted: list[tuple[int, dict[str, np.ndarray]]],
):
    total = sum(n for n, _ in weighted)

    if total <= 0:
        raise ValueError("no samples")

    keys = set(weighted[0][1])

    if any(set(s) != keys for _, s in weighted):
        raise ValueError("state key mismatch")

    return {
        k: sum(
            (n / total) * s[k].astype(np.float64)
            for n, s in weighted
        ).astype(weighted[0][1][k].dtype)
        for k in sorted(keys)
    }