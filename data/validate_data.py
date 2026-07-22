"""
생성된 합성 데이터(users/orders/events.csv)가 generate_data.py의 PERSONA_RULES
설계 의도를 실제로 잘 반영하고 있는지 KL divergence로 검증한다.

- 이산 확률(장바구니 전환율, 구매 전환율, 쿠폰 사용률): Bernoulli KL divergence
- 연속 분포(주문 금액): 히스토그램 기반 KL divergence, 이론값은 설계된
  Uniform(avg_order_value_low, avg_order_value_high) 분포

실행: python data/validate_data.py
"""

import numpy as np
import pandas as pd

from generate_data import PERSONA_RULES

EPS = 1e-9


def bernoulli_kl(p_empirical, p_theoretical):
    """KL(empirical || theoretical) for a Bernoulli distribution"""
    p = min(max(p_empirical, EPS), 1 - EPS)
    q = min(max(p_theoretical, EPS), 1 - EPS)
    return p * np.log(p / q) + (1 - p) * np.log((1 - p) / (1 - q))


def histogram_kl(samples, low, high, bins=10):
    """empirical(samples) vs 이론적 Uniform(low, high) 히스토그램 KL divergence"""
    edges = np.linspace(low, high, bins + 1)
    empirical_counts, _ = np.histogram(samples, bins=edges)
    empirical_p = empirical_counts / max(empirical_counts.sum(), 1)
    theoretical_p = np.full(bins, 1 / bins)  # uniform -> 각 구간 동일 확률

    empirical_p = np.clip(empirical_p, EPS, None)
    theoretical_p = np.clip(theoretical_p, EPS, None)
    return float(np.sum(empirical_p * np.log(empirical_p / theoretical_p)))


def main():
    users = pd.read_csv("data/users.csv")
    orders = pd.read_csv("data/orders.csv")
    events = pd.read_csv("data/events.csv")

    orders = orders.merge(users[["user_id", "persona_type"]], on="user_id")
    events = events.merge(users[["user_id", "persona_type"]], on="user_id")

    rows = []
    for persona, rules in PERSONA_RULES.items():
        ev = events[events["persona_type"] == persona]
        ords = orders[orders["persona_type"] == persona]

        page_views = (ev["event_type"] == "page_view").sum()
        product_views = (ev["event_type"] == "product_view").sum()
        cart_adds = (ev["event_type"] == "add_to_cart").sum()
        purchases = (ev["event_type"] == "purchase").sum()

        # 장바구니 담기 확률 = 1 - cart_abandon_rate (product_view 대비)
        empirical_cart_rate = cart_adds / product_views if product_views else 0
        theoretical_cart_rate = 1 - rules["cart_abandon_rate"]
        kl_cart = bernoulli_kl(empirical_cart_rate, theoretical_cart_rate)

        # 구매 확률 = purchase_prob (add_to_cart 대비)
        empirical_purchase_rate = purchases / cart_adds if cart_adds else 0
        theoretical_purchase_rate = rules["purchase_prob"]
        kl_purchase = bernoulli_kl(empirical_purchase_rate, theoretical_purchase_rate)

        # 쿠폰 사용률
        empirical_coupon_rate = ords["coupon_used"].mean() if len(ords) else 0
        theoretical_coupon_rate = min(rules["discount_sensitivity"] + 0.2, 0.9)
        kl_coupon = bernoulli_kl(empirical_coupon_rate, theoretical_coupon_rate)

        # 주문 금액 분포 (Uniform 설계 대비)
        low, high = rules["avg_order_value"]
        kl_aov = histogram_kl(ords["total_amount"].values, low, high) if len(ords) else float("nan")

        rows.append({
            "persona": persona,
            "n_orders": len(ords),
            "cart_rate(emp/theo)": f"{empirical_cart_rate:.3f}/{theoretical_cart_rate:.3f}",
            "KL_cart": round(kl_cart, 4),
            "purchase_rate(emp/theo)": f"{empirical_purchase_rate:.3f}/{theoretical_purchase_rate:.3f}",
            "KL_purchase": round(kl_purchase, 4),
            "coupon_rate(emp/theo)": f"{empirical_coupon_rate:.3f}/{theoretical_coupon_rate:.3f}",
            "KL_coupon": round(kl_coupon, 4),
            "KL_aov_uniform": round(kl_aov, 4),
        })

    result = pd.DataFrame(rows)
    pd.set_option("display.width", 160)
    print(result.to_string(index=False))

    print("\n기준: KL < 0.05 매우 근접 / 0.05~0.2 양호 / > 0.2 설계 의도와 괴리 (표본 부족 또는 로직 버그 의심)")


if __name__ == "__main__":
    main()
