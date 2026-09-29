from profile_mapping import match_profile_value


PROFILE = {
    "ho_ten": "Nguyen Van A",
    "email": "user@example.test",
    "lop": "Your Class",
    "chuyen_nganh": ["Computer Science"],
}


def test_exact_known_label_aliases():
    assert match_profile_value(" Full Name ", PROFILE) == "Nguyen Van A"
    assert match_profile_value("HỌ VÀ TÊN *", PROFILE) == "Nguyen Van A"
    assert match_profile_value("ho_ten", PROFILE) is None


def test_unknown_template_and_non_scalar_values_are_not_suggested():
    assert match_profile_value("First name", PROFILE) is None
    assert match_profile_value("Class", PROFILE) is None
    assert match_profile_value("Major", PROFILE) is None
import ast
from pathlib import Path


def test_example_profile_contains_only_empty_template_values():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "config_example.py").read_text())
    profile = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                   and node.target.id == "USER_PROFILE")
    assert all(value == "" or value == [] for value in profile.values())
