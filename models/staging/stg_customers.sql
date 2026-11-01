-- Latest version of each customer across all CSV drops (later drops win; within a drop, the last row wins).
select customer_id, email, full_name, company_name, phone, country_code, segment, signup_date, is_active,
       _load_id as last_load_id
from (
    select *, row_number() over (partition by customer_id order by _load_seq desc, _source_row desc) as rn
    from {{ source('landing', 'customers') }}
) t
where rn = 1
