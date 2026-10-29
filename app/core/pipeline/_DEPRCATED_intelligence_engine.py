scores = {
    "market": 12,
    "chop": 8,
    "trend": 10,
    "bias": 15,
    "stability": 7,
    "pattern": 10,
    "confirm": 8,
    "htf": 5,
    "h4": 3,
    "volatility": 2
}

weights = {
    "market": 1.2,
    "chop": 1.4,
    "trend": 1.5,
    "bias": 1.8,
    "stability": 1.3,
    "pattern": 1.1,
    "confirm": 1.0,
    "htf": 2.2,
    "h4": 1.6,
    "volatility": 2.0
}

def compute_score(scores, weights):
    weighted_sum = 0
    weight_total = 0

    for k in scores:
        w = weights.get(k, 1.0)
        weighted_sum += scores[k] * w
        weight_total += w

    return weighted_sum / weight_total

score = compute_score(scores, weights)

max_possible = sum(15 * weights[k] for k in weights)
confidence = (score / max_possible) * 100

if confidence >= 85:
    grade = "A"
elif confidence >= 70:
    grade = "B"
elif confidence >= 55:
    grade = "C"
elif confidence >= 40:
    grade = "D"
elif confidence >= 25:
    grade = "E"
else:
    grade = "F"

qualified = confidence >= 65

result = {
    "score": score,
    "confidence": confidence,
    "grade": grade,
    "qualified": qualified
}

print(result)