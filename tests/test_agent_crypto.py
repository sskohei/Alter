import base64
import os
import uuid

import pytest
from alter_domain.agent_models import AgentDraftInput, ProfileRevision
from alter_domain.profile_crypto import ProfileCipher
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError


def test_private_encryption_is_random_and_bound_to_agent_and_version():
    cipher = ProfileCipher(base64.b64encode(os.urandom(32)).decode())
    agent = uuid.uuid4()
    value = cipher.encrypt("秘密", agent, 1)
    assert value != cipher.encrypt("秘密", agent, 1)
    assert cipher.decrypt(value, agent, 1) == "秘密"
    with pytest.raises(InvalidTag):
        cipher.decrypt(value, uuid.uuid4(), 1)
    with pytest.raises(InvalidTag):
        cipher.decrypt(value, agent, 2)


def test_private_not_part_of_public_schema_and_unverified_icons_rejected():
    assert "private_info" not in ProfileRevision.model_fields
    with pytest.raises(ValidationError):
        AgentDraftInput(
            name="AI",
            personality="",
            values_text="",
            interests=[],
            speaking_style="",
            interaction_policy="",
            shareable_info="",
            restrictions="",
            icon_path="other/a.png",
        )
