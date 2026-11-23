import math

from flask import current_app

PLAN_ORDER = ("free", "pro", "max")
PASS_MONTHS = {"pass3": 3, "pass6": 6}
PASS_DAYS_PER_MONTH = 30
QUIZ_CREDITS_PER_TEN = {"easy": 1, "medium": 1, "hard": 2, "god mode": 4}
FLASHCARD_CREDITS = 1


def plan_table():
    cfg = current_app.config
    return {
        "free": {"name": "Free", "price_inr": 0, "credits": cfg["FREE_CREDITS"]},
        "pro": {"name": "Pro", "price_inr": cfg["PRO_PRICE_INR"], "credits": cfg["PRO_CREDITS"]},
        "max": {"name": "Max", "price_inr": cfg["MAX_PRICE_INR"], "credits": cfg["MAX_CREDITS"]},
    }


def plan_credits(plan):
    return plan_table()[plan]["credits"]


def monthly_paise(plan):
    return plan_table()[plan]["price_inr"] * 100


def pass_paise(plan, months):
    total = monthly_paise(plan) * months
    discount = current_app.config["PASS_DISCOUNT_PERCENT"]
    return total - (total * discount) // 100


def quiz_cost(difficulty, total_questions):
    per_ten = QUIZ_CREDITS_PER_TEN.get(str(difficulty).lower(), 1)
    return math.ceil(int(total_questions) / 10) * per_ten


def notes_cost(pages):
    return max(1, int(pages))


def display_name(plan):
    return plan_table().get(plan, {"name": "Free"})["name"]