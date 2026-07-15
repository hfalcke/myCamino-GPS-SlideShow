#!/usr/bin/env python3
"""Compatibility export for the shared GPX elevation processor."""

from gpx_processing import elevation_gain_loss, smoothed_elevation_profile

__all__ = ["elevation_gain_loss", "smoothed_elevation_profile"]
