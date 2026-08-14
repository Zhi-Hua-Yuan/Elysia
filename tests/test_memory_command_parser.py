import pytest

from open_llm_vtuber.memory import (
    MemoryCategory,
    MemoryCommandKind,
    MemoryCommandParseReason,
    MemoryCommandParseStatus,
    MemoryCommandParser,
    normalize_memory_match_text,
    normalize_memory_text,
    parse_memory_command,
)


@pytest.mark.parametrize(
    ("text", "expected_value"),
    [
        ("以后叫我阿源", "阿源"),
        ("以后请叫我：阿源。", "阿源"),
        ("请叫我 阿源", "阿源"),
        ("  请叫我，舰长！ ", "舰长"),
        ('以后叫我 "Captain"', "Captain"),
    ],
)
def test_parser_recognizes_preferred_address(
    text: str,
    expected_value: str,
) -> None:
    result = parse_memory_command(text)

    assert result.status == MemoryCommandParseStatus.RECOGNIZED
    assert result.command is not None
    assert result.command.kind == MemoryCommandKind.SAVE
    assert result.command.category == MemoryCategory.PREFERRED_ADDRESS
    assert result.command.value == expected_value


@pytest.mark.parametrize(
    "text",
    [
        "记住，我不喜欢太甜的饮料",
        "请记住我喜欢古典音乐",
        "帮我记住，我偏好安静的环境",
        "我希望你记住，我习惯早睡",
        "记住，回答时先给结论",
        "记住，回复时尽量简短",
        "记住，和我说话时不要太正式",
        "你要记住，请尽量使用简体中文",
    ],
)
def test_parser_classifies_explicit_preferences(text: str) -> None:
    result = parse_memory_command(text)

    assert result.status == MemoryCommandParseStatus.RECOGNIZED
    assert result.command is not None
    assert result.command.kind == MemoryCommandKind.SAVE
    assert result.command.category == MemoryCategory.PREFERENCE


@pytest.mark.parametrize(
    ("text", "expected_value"),
    [
        ("记住，我周末通常要加班", "我周末通常要加班"),
        ("请记住我养了一只猫", "我养了一只猫"),
        ("帮我记住：我的生日在五月", "我的生日在五月"),
        ("我希望你记住，我正在学习日语", "我正在学习日语"),
        ("记住，下个月我要参加考试", "下个月我要参加考试"),
        ("记住我每周三有固定课程", "我每周三有固定课程"),
    ],
)
def test_parser_classifies_other_explicit_facts(
    text: str,
    expected_value: str,
) -> None:
    result = parse_memory_command(text)

    assert result.command is not None
    assert result.command.category == MemoryCategory.IMPORTANT_FACT
    assert result.command.value == expected_value


@pytest.mark.parametrize(
    "text",
    [
        "你记得我什么",
        "你都记得我什么？",
        "查看我的记忆",
        "列出你的持久记忆。",
    ],
)
def test_parser_recognizes_explicit_list_commands(text: str) -> None:
    result = parse_memory_command(text)

    assert result.command == result.command.model_copy(
        update={"kind": MemoryCommandKind.LIST}
    )


@pytest.mark.parametrize(
    ("text", "target"),
    [
        ("忘掉我不喜欢甜食这件事", "我不喜欢甜食"),
        ("忘记关于周末加班的记忆", "周末加班"),
        ("请忘掉：喜欢太甜的饮料", "喜欢太甜的饮料"),
        ("删除“不喜欢甜食”这条记忆", "不喜欢甜食"),
        ("请删除关于周末加班这项记忆", "周末加班"),
        ("删除 '固定课程' 这条记忆", "固定课程"),
    ],
)
def test_parser_extracts_delete_targets(text: str, target: str) -> None:
    result = parse_memory_command(text)

    assert result.status == MemoryCommandParseStatus.RECOGNIZED
    assert result.command is not None
    assert result.command.kind == MemoryCommandKind.DELETE
    assert result.command.target == target


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("清空所有记忆", MemoryCommandKind.CLEAR),
        ("请清空全部持久记忆", MemoryCommandKind.CLEAR),
        ("忘掉所有关于我的记忆", MemoryCommandKind.CLEAR),
        ("确认清空记忆", MemoryCommandKind.CONFIRM_CLEAR),
        ("请确认清空所有记忆", MemoryCommandKind.CONFIRM_CLEAR),
        ("取消清空", MemoryCommandKind.CANCEL_CLEAR),
        ("取消清空全部记忆", MemoryCommandKind.CANCEL_CLEAR),
        ("不要清空了", MemoryCommandKind.CANCEL_CLEAR),
    ],
)
def test_parser_prioritizes_clear_control_commands(
    text: str,
    kind: MemoryCommandKind,
) -> None:
    result = parse_memory_command(text)

    assert result.status == MemoryCommandParseStatus.RECOGNIZED
    assert result.command is not None
    assert result.command.kind == kind


@pytest.mark.parametrize(
    "text",
    [
        "我今天有点累",
        "他说让我记住这件事",
        "如果一个 AI 能记住用户会怎样",
        "你还记得我吗",
        "你记得昨天发生了什么吗",
        "人的记忆可靠吗",
        "叫我看看这个页面",
        "我忘记带钥匙了",
        "我想删除一个文件",
    ],
)
def test_parser_leaves_ordinary_conversation_untouched(text: str) -> None:
    result = parse_memory_command(text)

    assert result.status == MemoryCommandParseStatus.NOT_COMMAND
    assert result.command is None
    assert result.reason is None


@pytest.mark.parametrize(
    "text",
    ["不要记住这件事", "别记住我刚才说的", "不用记住"],
)
def test_parser_rejects_negated_save_commands(text: str) -> None:
    result = parse_memory_command(text)

    assert result.status == MemoryCommandParseStatus.INVALID
    assert result.reason == MemoryCommandParseReason.NEGATED_COMMAND


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("记住", MemoryCommandParseReason.EMPTY_VALUE),
        ("请叫我", MemoryCommandParseReason.EMPTY_VALUE),
        ("忘掉", MemoryCommandParseReason.INVALID_TARGET),
        (
            "记住我喜欢茶，然后清空所有记忆",
            MemoryCommandParseReason.UNSUPPORTED_FORM,
        ),
        (
            "请叫我阿源，然后删除所有记忆",
            MemoryCommandParseReason.UNSUPPORTED_FORM,
        ),
    ],
)
def test_parser_rejects_incomplete_or_conflicting_commands(
    text: str,
    reason: MemoryCommandParseReason,
) -> None:
    result = parse_memory_command(text)

    assert result.status == MemoryCommandParseStatus.INVALID
    assert result.reason == reason


def test_parser_rejects_control_characters_only_for_explicit_commands() -> None:
    command = parse_memory_command("记住，第一行\n第二行")
    ordinary = parse_memory_command("普通对话\n第二行")

    assert command.reason == MemoryCommandParseReason.UNSUPPORTED_FORM
    assert ordinary.status == MemoryCommandParseStatus.NOT_COMMAND


def test_parser_bounds_only_explicit_command_inputs() -> None:
    parser = MemoryCommandParser(max_command_chars=20)
    command = parser.parse("记住，" + "花" * 30)
    ordinary = parser.parse("普通聊天" + "花" * 30)

    assert command.reason == MemoryCommandParseReason.INPUT_TOO_LONG
    assert ordinary.status == MemoryCommandParseStatus.NOT_COMMAND


def test_normalization_is_idempotent_and_has_separate_match_semantics() -> None:
    presentation = normalize_memory_text("  Ｃａｐｔａｉｎ   喜欢花！！ ")
    match_value = normalize_memory_match_text(" Captain，喜欢花！ ")

    assert presentation == "Captain 喜欢花"
    assert normalize_memory_text(presentation) == presentation
    assert match_value == "captain喜欢花"
    assert normalize_memory_match_text(match_value) == match_value
