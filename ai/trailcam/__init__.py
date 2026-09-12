"""The laptop side of the wildlife cameras.

The cameras in the woods decide what to photograph with a few hundred lines
of arithmetic (see ../../step8_reject_shadows.py).  This package is the
workshop behind that: it runs a research-grade detector over the unbiased
training bursts so we can find out how good those few hundred lines really
are.

See ../design.md and ../evaluation-design.md.  This is milestone E1, Label.
"""

__all__ = ["config", "manifest", "bursts", "detector", "detect", "report"]
