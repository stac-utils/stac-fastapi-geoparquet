import stac_fastapi.geoparquet.api

api = stac_fastapi.geoparquet.api.create()
app = api.app
