select sku, product_name, category, unit_price, currency, is_discontinued
from {{ ref('stg_products') }}
