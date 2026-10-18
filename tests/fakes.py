from copy import deepcopy


class FakeStripe:
    def __init__(self):
        self.customers_created = 0
        self.checkout_attempt_ids = []
        self.sessions = {}
        self.subscriptions = {}

    def create_customer(self, tenant_id, customer_key):
        self.customers_created += 1
        return f"cus_{tenant_id}"

    def validate_price(self):
        pass

    def create_checkout(self, tenant_id, customer_id, attempt_id):
        self.checkout_attempt_ids.append(attempt_id)
        session = {"id": f"cs_test_{attempt_id}", "livemode": False,
                   "url": "https://checkout.stripe.com/c/pay/test_fixture", "status": "open"}
        self.sessions[session["id"]] = session
        return session

    def retrieve_checkout(self, session_id):
        return self.sessions[session_id]

    def retrieve_subscription(self, subscription_id):
        return deepcopy(self.subscriptions[subscription_id])


def subscription(*, sub_id="sub_a", customer="cus_tenant_a", status="active", price="price_pro", livemode=False):
    return {"id": sub_id, "object": "subscription", "customer": customer, "status": status,
            "livemode": livemode, "items": {"data": [{"price": {"id": price}, "quantity": 1}]}}


def event(event_id="evt_fixture", *, event_type="checkout.session.completed", customer="cus_tenant_a", sub_id="sub_a"):
    return {"id": event_id, "object": "event", "type": event_type, "livemode": False,
            "data": {"object": {"id": "cs_test_fixture" if event_type.startswith("checkout") else sub_id,
                                "object": "checkout.session" if event_type.startswith("checkout") else "subscription",
                                "customer": customer, "subscription": sub_id}}}

