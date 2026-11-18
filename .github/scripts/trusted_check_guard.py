"""Shared validation for verified Terraform check inputs."""

import re


class GuardError(Exception):
    pass


def require(condition):
    if not condition:
        raise GuardError()


def positive_int(value):
    require(type(value) is int and value > 0)
    return value


def head_sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value))
    return value
