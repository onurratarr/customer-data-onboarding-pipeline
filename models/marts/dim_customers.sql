-- No email or phone in the marts layer: contact PII stays in staging.
with o as (
    select customer_id, count(*) as order_count, min(order_ts) as first_order_at, max(order_ts) as last_order_at,
           sum(case when status in ('shipped', 'delivered') then line_amount else 0 end) as lifetime_revenue
    from {{ ref('stg_orders') }}
    group by customer_id
)
select c.customer_id, c.full_name, c.company_name, c.country_code, c.segment, c.signup_date, c.is_active,
       coalesce(o.order_count, 0) as order_count, o.first_order_at, o.last_order_at,
       coalesce(o.lifetime_revenue, 0) as lifetime_revenue
from {{ ref('stg_customers') }} c
left join o on o.customer_id = c.customer_id
