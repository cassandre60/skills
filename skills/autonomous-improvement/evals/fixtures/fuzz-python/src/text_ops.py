def normalize(text: str) -> str:
    """Seeded bug (Stream C fixture): lowercases only the FIRST 'A'.

    The property the harness holds is idempotence:
    ``normalize(normalize(x)) == normalize(x)``. The one-shot replacement
    breaks it for any input containing two uppercase As:

        normalize('AA') == 'aA', but normalize('aA') == 'aa' != 'aA'

    Inputs without a doubled 'A' pass, which is exactly why shrinking
    matters: the recorded seed is a long sentence, and the minimal
    counterexample is 'AA'. The Builder fix lowercases every character.
    """
    return text.replace("A", "a", 1)
