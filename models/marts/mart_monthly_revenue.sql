-- Revenue is reported per currency; no FX conversion is invented.
select cast(date_trunc('month', order_ts) as date) as month, currency, segment,
       count(*) as orders,
       sum(quantity) as units,
       sum(recognized_revenue) as revenue,
       sum(case when status = 'cancelled' then 1 else 0 end) as cancelled_orders,
       sum(case when status = 'returned' then 1 else 0 end) as returned_orders
from {{ ref('fct_orders') }}
where product_known
group by 1, 2, 3
