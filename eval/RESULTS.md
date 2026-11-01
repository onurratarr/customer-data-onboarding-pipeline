# Mapping eval results (synthetic data)

> SYNTHETIC DATA: hand-labelled gold mappings for fictional sources; not a client benchmark. Mapper: `mock-lexicon-v1`. Regenerate with `python -m cdop eval-mapping --write`.

| split | policy | columns | gold mappings | proposed | correct | precision | recall | decoys left unmapped |
|---|---|---|---|---|---|---|---|---|
| dev | all proposals | 54 | 46 | 46 | 46 | 100.0% | 100.0% | 8/8 |
| dev | auto-accept conf >=0.6 | 54 | 46 | 46 | 46 | 100.0% | 100.0% | 8/8 |
| dev | auto-accept conf >=0.8 | 54 | 46 | 45 | 45 | 100.0% | 97.8% | 8/8 |
| dev | auto-accept conf >=0.9 | 54 | 46 | 43 | 43 | 100.0% | 93.5% | 8/8 |
| heldout | all proposals | 54 | 46 | 43 | 40 | 93.0% | 87.0% | 7/8 |
| heldout | auto-accept conf >=0.6 | 54 | 46 | 33 | 32 | 97.0% | 69.6% | 8/8 |
| heldout | auto-accept conf >=0.8 | 54 | 46 | 11 | 11 | 100.0% | 23.9% | 8/8 |
| heldout | auto-accept conf >=0.9 | 54 | 46 | 9 | 9 | 100.0% | 19.6% | 8/8 |

## Per source (all proposals)

| source | split | columns | precision | recall | decoys left unmapped |
|---|---|---|---|---|---|
| crm_export | dev | 27 | 100.0% | 100.0% | 4/4 |
| erp_heldout | heldout | 27 | 95.5% | 91.3% | 3/4 |
| halvorsen | dev | 27 | 100.0% | 100.0% | 4/4 |
| shop_heldout | heldout | 27 | 90.5% | 82.6% | 4/4 |

## Every disagreement with the gold mapping

| source | entity | column | gold | proposed | confidence |
|---|---|---|---|---|---|
| erp_heldout | products | `MAT_GROUP` | category | (unmapped) | 0.55 |
| erp_heldout | products | `BASE_UOM` | (unmapped) | category | 0.45 |
| erp_heldout | orders | `SOLD_TO` | customer_id | (unmapped) | 0.55 |
| shop_heldout | customers | `org` | company_name | (unmapped) | 0.55 |
| shop_heldout | customers | `registered_at` | signup_date | (unmapped) | 0.64 |
| shop_heldout | orders | `order_ref` | order_id | customer_id | 0.50 |
| shop_heldout | orders | `buyer_id` | customer_id | order_id | 0.78 |
