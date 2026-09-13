import json

from bugops.sandbox.test_command import detect_test_command


def test_detect_test_command_uses_override_map(tmp_path):
    result = detect_test_command(tmp_path, {"myorg/backend-api": "custom test cmd"}, "myorg", "backend-api")
    assert result == ("npm install", "custom test cmd")


def test_detect_test_command_npm_default_without_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    result = detect_test_command(tmp_path, {}, "myorg", "backend-api")
    assert result == ("npm install", "npm test")


def test_detect_test_command_npm_ci_with_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "package-lock.json").write_text("{}")
    result = detect_test_command(tmp_path, {}, "myorg", "backend-api")
    assert result == ("npm ci", "npm test")


def test_detect_test_command_pnpm(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    result = detect_test_command(tmp_path, {}, "myorg", "backend-api")
    assert result == ("pnpm install --frozen-lockfile", "pnpm test")


def test_detect_test_command_yarn(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    (tmp_path / "yarn.lock").write_text("")
    result = detect_test_command(tmp_path, {}, "myorg", "backend-api")
    assert result == ("yarn install --frozen-lockfile", "yarn test")


def test_detect_test_command_no_package_json(tmp_path):
    assert detect_test_command(tmp_path, {}, "myorg", "backend-api") is None


def test_detect_test_command_no_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}))
    assert detect_test_command(tmp_path, {}, "myorg", "backend-api") is None
