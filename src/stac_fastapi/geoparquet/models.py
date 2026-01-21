import stac_fastapi.api.models
from stac_fastapi.extensions.core.fields import FieldsExtension
from stac_fastapi.extensions.core.filter import SearchFilterExtension
from stac_fastapi.extensions.core.pagination import OffsetPaginationExtension
from stac_fastapi.extensions.core.sort import SortExtension
from stac_fastapi.types.search import BaseSearchPostRequest

from .search import FixedSearchGetRequest

EXTENSIONS = [
    OffsetPaginationExtension(),
    SearchFilterExtension(),
    FieldsExtension(),
    SortExtension(),
]

GetSearchRequestModel = stac_fastapi.api.models.create_get_request_model(
    base_model=FixedSearchGetRequest, extensions=EXTENSIONS
)
PostSearchRequestModel = stac_fastapi.api.models.create_post_request_model(
    base_model=BaseSearchPostRequest, extensions=EXTENSIONS
)
