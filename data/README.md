# Data

Example data, also used for tests.
To create [collections.json](./collections.json):

```shell
scripts/generate-collections
```

## Smaller collections

The `naip-10` parquet file was created via the following command:

```shell
rustac translate data/naip.parquet | jq '{ features: .features[:10], type: "FeatureCollection" }' | rustac translate - data/naip-10.parquet
```

The `openaerialmap-10` was done the same way.
