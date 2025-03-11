from stac_fastapi.geoparquet import create_api

api = create_api()
app = api.app
