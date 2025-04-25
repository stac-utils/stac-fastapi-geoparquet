import stac_fastapi.api.models
from stac_fastapi.extensions.core.pagination import OffsetPaginationExtension

from .search import SearchGetRequest, SearchPostRequest

GetSearchRequestModel = stac_fastapi.api.models.create_request_model(
    model_name="SearchGetRequest",
    base_model=SearchGetRequest,
    mixins=[OffsetPaginationExtension().GET],
    request_type="GET",
)
PostSearchRequestModel = stac_fastapi.api.models.create_request_model(
    model_name="SearchPostRequest",
    base_model=SearchPostRequest,
    mixins=[OffsetPaginationExtension().POST],
    request_type="POST",
)
