select order_id, customer_id, sku, category, segment, country_code, quantity, unit_price, line_amount, currency,
       status, order_ts, cast(order_ts as date) as order_date, updated_at, customer_known, product_known,
       case when status in ('shipped', 'delivered') then line_amount else 0 end as recognized_revenue
from {{ ref('int_orders_enriched') }}
