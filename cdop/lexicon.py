"""Header vocabulary for the offline mock mapper. Written from generic data-modelling vocabulary plus the two
"dev" sources (halvorsen, crm_export). Deliberately NOT extended with anything specific to the held-out sources,
so the held-out eval split shows how the mock does on headers it was not written for."""

ABBREVIATIONS = {"cust": "customer", "no": "number", "nbr": "number", "num": "number", "#": "number",
                 "qty": "quantity", "amt": "amount", "addr": "address", "desc": "description", "cd": "code",
                 "dt": "date", "ts": "timestamp", "tel": "phone", "ctry": "country", "cntry": "country",
                 "curr": "currency", "prod": "product", "disc": "discontinued", "flg": "flag", "id": "id",
                 "modstamp": "modified", "iso": "iso"}
STOPWORDS = {"is", "the", "of", "on", "at", "flag", "1", "2"}

SYNONYMS = {
    "customers": {
        "customer_id": ["customer id", "customer number", "customer", "account id", "account number",
                        "client id", "client number", "customer code"],
        "email": ["email", "email address", "primary email", "contact email"],
        "full_name": ["contact name", "full name", "name", "contact full name", "contact"],
        "company_name": ["company", "company name", "account name", "account", "business name", "organization"],
        "phone": ["phone", "phone number", "telephone", "mobile phone", "mobile"],
        "country_code": ["country", "country code", "billing country", "nation"],
        "segment": ["segment", "customer segment", "tier", "customer tier"],
        "signup_date": ["signup date", "sign up date", "created on", "created date", "account created date",
                        "created", "registration date", "created at"],
        "is_active": ["active", "active status", "enabled status"],
    },
    "products": {
        "sku": ["sku", "item code", "product code", "item number", "product id", "item id", "part number"],
        "product_name": ["product name", "description", "item name", "name", "product", "product description"],
        "category": ["category", "product line", "family", "product family", "product category"],
        "unit_price": ["unit price", "list price", "price", "msrp"],
        "currency": ["currency", "currency code", "currency iso code"],
        "is_discontinued": ["discontinued", "archived", "obsolete", "inactive"],
    },
    "orders": {
        "order_id": ["order id", "order number", "id", "order"],
        "customer_id": ["customer id", "customer number", "customer", "account number", "account ref",
                        "account id", "client id"],
        "sku": ["sku", "item", "item code", "product code", "product id"],
        "quantity": ["quantity", "quantity ordered"],
        "unit_price": ["unit price", "price"],
        "order_ts": ["order date", "placed at", "order timestamp", "effective date", "created at", "order time",
                     "ordered at"],
        "status": ["status", "order status", "state"],
        "updated_at": ["updated at", "modified", "last modified", "modified at", "system modified", "updated"],
    },
}

# canonical type -> {profile evidence kind: weight}
TYPE_EVIDENCE = {
    "id": {"id_like": 1.0, "integer": 0.6},
    "email": {"email": 1.0},
    "person_name": {"name_like": 1.0, "text": 0.5},
    "org_name": {"name_like": 0.8, "text": 0.8},
    "phone": {"phone": 1.0},
    "country": {"country": 1.0},
    "category": {"category": 1.0},
    "date": {"date": 1.0, "timestamp": 0.8},
    "timestamp": {"timestamp": 1.0, "date": 0.6},
    "boolean": {"boolean": 1.0},
    "money": {"decimal": 1.0},
    "integer": {"integer": 1.0},
    "currency": {"currency": 1.0},
    "text": {"text": 1.0, "name_like": 0.6},
}
