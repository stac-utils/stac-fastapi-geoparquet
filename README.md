# stac-fastapi-geoparquet

[![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/stac-utils/stac-fastapi-geoparquet/ci.yml?style=for-the-badge)](https://github.com/stac-utils/stac-fastapi-geoparquet/actions/workflows/ci.yml)
[![PyPI - Version](https://img.shields.io/pypi/v/stac-fastapi-geoparquet?style=for-the-badge)](https://pypi.org/project/stac-fastapi-geoparquet/)
[![GitHub License](https://img.shields.io/github/license/stac-utils/stac-fastapi-pgstac?style=for-the-badge)](https://github.com/stac-utils/stac-fastapi-geoparquet/blob/main/LICENSE)

A [stac-fastapi](https://github.com/stac-utils/stac-fastapi) with a [stac-geoparquet](https://github.com/stac-utils/stac-geoparquet/blob/main/spec/stac-geoparquet-spec.md) backend.

**stac-fastapi-geoparquet** can serve a full-featured STAC API from one or more **stac-geoparquet** files located (e.g.) in blob storage — no database required.

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

This will start the server on <http://127.0.0.1:8000>.
The collection will be auto-generated from the items in the **stac-geoparquet** file.

### Using collections

Instead of providing the href to a single file, you can provide the href to a file containing a JSON list of collections.
Any [collection assets](https://github.com/radiantearth/stac-spec/blob/master/collection-spec/collection-spec.md#assets) with a [application/vnd.apache.parquet](https://github.com/opengeospatial/geoparquet/blob/main/format-specs/geoparquet.md#media-type) `type` field will be added to the server as sources of items.
For an example, see [data/collections.json](./data/collections.json).

To start a server with one or more collections:

```shell
$ STAC_FASTAPI_COLLECTIONS_HREF=data/collections.json uvicorn stac_fastapi.geoparquet.main:app
INFO:     Started server process [47920]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

To auto-generate the collections file, we provide a [script](./scripts/generate-collections):

```shell
scripts/generate-collections s3://my-bucket/a.parquet s3://my-bucket/b.parquet
```

This will update `./data/collections.json`.

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
