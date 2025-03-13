# stac-fastapi-geoparquet

A [stac-fastapi](https://github.com/stac-utils/stac-fastapi) implementation with [stac-geoparquet](https://github.com/stac-utils/stac-geoparquet/blob/main/spec/stac-geoparquet-spec.md) as a backend.

!!! warning "Under construction 👷"

    This project is under active development and may break at any time.

## Running locally

<!-- markdownlint-disable code-block-style -->

```shell
python -m pip install 'stac-fastapi-geoparquet[serve]'
export STAC_FASTAPI_GEOPARQUET_HREF=path/to/naip.parquet
uvicorn stac_fastapi.geoparquet.main:app
```

This will start a STAC API server on <http://127.0.0.1:8000>.

## Deploying

An example AWS CDK application to deploy **stac-fastapi-geoparquet** can be found at <https://github.com/stac-utils/stac-fastapi-geoparquet/tree/main/infrastructure/aws>.
