# stac-fastapi-geoparquet

A [stac-fastapi](https://github.com/stac-utils/stac-fastapi) with a [stac-geoparquet](https://github.com/stac-utils/stac-geoparquet/blob/main/spec/stac-geoparquet-spec.md) backend.

**stac-fastapi-geoparquet** can serve a full-featured STAC API from a **stac-geoparquet** file located (e.g.) in blob storage — no database required.

> [!WARNING]
> 👷 This project is under active development and may change and break at any time.

## Usage

To start a STAC API server pointing to a single **stac-geoparquet** file:

```shell
$ python -m pip install 'stac-fastapi-geoparquet[serve]'
$ STAC_FASTAPI_GEOPARQUET_HREF=data/naip.parquet uvicorn stac_fastapi.geoparquet.main:app
INFO:     Started server process [47920]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

To explore the API, you can use [stac browser](https://radiantearth.github.io/stac-browser/#/external/http:/127.0.0.1:8000/?.language=en).

## Development

Get [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```shell
git clone git@github.com:stac-utils/stac-fastapi-geoparquet.git
cd stac-fastapi-geoparquet
uv sync
```

To run the tests:

```shell
uv run pytest
```
