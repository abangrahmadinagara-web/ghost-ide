"""Account safety checks."""


def check_password_strength(password):
    problems = []
    if len(password) < 12:
        problems.append("too short")
    if not any(ch.isdigit() for ch in password):
        problems.append("needs a digit")
    if not any(ch.isupper() for ch in password):
        problems.append("needs an uppercase letter")
    if not any(ch.islower() for ch in password):
        problems.append("needs a lowercase letter")
    if not any(not ch.isalnum() for ch in password):
        problems.append("needs a symbol")
    return problems
