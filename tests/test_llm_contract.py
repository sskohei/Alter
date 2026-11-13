from decimal import Decimal

import pytest
from alter_domain.llm_contract import ModelPrice


def test_cost_rounds_up_and_rejects_negative_tokens():
    price = ModelPrice(
        provider="fake", model="fake", input_per_million="0.3", output_per_million="2"
    )
    assert price.cost(1, 1) == Decimal("0.000003")
    assert price.cost(0, 0) == 0
    with pytest.raises(ValueError):
        price.cost(-1, 0)
