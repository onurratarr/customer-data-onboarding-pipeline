select o.order_id, o.customer_id, o.sku, o.quantity, o.unit_price, o.line_amount, o.status, o.order_ts,
       o.updated_at, p.category, p.currency, c.segment, c.country_code,
       c.customer_id is not null as customer_known,
       p.sku is not null as product_known
from {{ ref('stg_orders') }} o
left join {{ ref('stg_customers') }} c on c.customer_id = o.customer_id
left join {{ ref('stg_products') }} p on p.sku = o.sku
