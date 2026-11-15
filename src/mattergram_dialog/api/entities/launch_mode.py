from enum import Enum


class LaunchMode(Enum):
    STANDARD = "standard"
    ROOT = "root"
    EXCLUSIVE = "exclusive"
    SINGLE_TOP = "single_top"
