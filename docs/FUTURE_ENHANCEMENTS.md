# Future Enhancements

This document records agreed upgrade ideas that are not part of the current
application behavior.

## Optional Satellite Map Providers

Add satellite imagery as an optional Track Map background while keeping
OpenStreetMap as the reliable default. There is no single global,
high-resolution, unrestricted satellite tile service equivalent to OSM, so
each provider needs its own capabilities and usage-policy handling.

Potential providers:

- [NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/access-basics/): public
  WMTS/WMS and XYZ-style access with little or no account setup. Many layers
  are scientific or lower resolution than a conventional satellite basemap.
- [Copernicus Data Space](https://dataspace.copernicus.eu/ogc-api): global
  Sentinel imagery through standards-based services. This is the preferred
  future option for users who already have a Copernicus/Sentinel account.
- [Sentinel Hub](https://docs.sentinel-hub.com/api/latest/api/ogc/wmts/):
  convenient processed Sentinel layers and WMTS access, requiring an account,
  instance configuration, and observance of service limits.
- [USGS National Map](https://apps.nationalmap.gov/services/): cached imagery
  services with strong United States coverage, but not a global provider.

A future provider adapter should support service URL, credentials or instance
ID, imagery layer, acquisition date/date strategy, cloud filtering,
attribution, maximum zoom, request timeout, and explicit download/cache policy.
Imagery-data openness and permission to bulk-download or cache rendered tiles
must be checked separately. Credentials belong in the user's macOS Keychain or
standalone preferences, not in an Adventure file committed or shared with
other users.
