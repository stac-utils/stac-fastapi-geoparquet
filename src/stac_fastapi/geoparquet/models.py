import stac_fastapi.api.models
from stac_fastapi.api.models import ItemCollectionUri
from stac_fastapi.extensions.core.fields import (
    FieldsConformanceClasses,
    FieldsExtension,
)
from stac_fastapi.extensions.core.filter import (
    FilterConformanceClasses,
    FilterExtension,
)
from stac_fastapi.extensions.core.pagination import OffsetPaginationExtension
from stac_fastapi.extensions.core.sort import SortExtension
from stac_fastapi.types.search import BaseSearchPostRequest

from .filters import FiltersClient
from .search import FixedSearchGetRequest

# The full Filter extension, rather than the search-only one: it registers
# `/queryables` and `/collections/{collection_id}/queryables` alongside the
# `filter` search parameter.
filter_extension = FilterExtension(client=FiltersClient())
filter_extension.conformance_classes.append(
    FilterConformanceClasses.ADVANCED_COMPARISON_OPERATORS
)
# `fields` works on the items endpoint too, not just search.
fields_extension = FieldsExtension()
fields_extension.conformance_classes.append(FieldsConformanceClasses.ITEMS)

EXTENSIONS = [
    OffsetPaginationExtension(),
    filter_extension,
    fields_extension,
    SortExtension(),
]

GetSearchRequestModel = stac_fastapi.api.models.create_get_request_model(
    base_model=FixedSearchGetRequest, extensions=EXTENSIONS
)
PostSearchRequestModel = stac_fastapi.api.models.create_post_request_model(
    base_model=BaseSearchPostRequest, extensions=EXTENSIONS
)
ItemsGetRequestModel = stac_fastapi.api.models.create_get_request_model(
    base_model=ItemCollectionUri, extensions=EXTENSIONS
)
