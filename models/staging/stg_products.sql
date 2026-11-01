select sku, product_name, category, unit_price, currency, is_discontinued
from (
    select *, row_number() over (partition by sku order by _load_seq desc, _source_row desc) as rn
    from {{ source('landing', 'products') }}
) t
where rn = 1
