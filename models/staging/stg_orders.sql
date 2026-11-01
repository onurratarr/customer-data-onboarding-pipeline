-- landing_orders is upserted by order_id on every API pull, so it already holds the current state of each order.
select order_id, customer_id, sku, quantity, unit_price,
       cast(quantity * unit_price as decimal(12, 2)) as line_amount,
       status, order_ts, updated_at
from {{ source('landing', 'orders') }}
