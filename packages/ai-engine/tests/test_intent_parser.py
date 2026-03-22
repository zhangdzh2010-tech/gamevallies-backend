import pytest


class IntentParser:
    """Parses game descriptions to determine game type"""

    def parse(self, description: str) -> dict:
        """Parse Chinese/English game description to determine game type"""
        if not description or not isinstance(description, str):
            return {"game_type": "dodge", "confidence": 0.3}

        description_lower = description.lower()

        # Define patterns for each game type
        patterns = {
            "dodge": [
                "躲避", "躲", "陨石", "避开", "闪避", "躲闪",
                "dodge", "avoid", "asteroid", "evade"
            ],
            "catcher": [
                "接", "水果", "捕捉", "抓", "收集", "接住",
                "catch", "fruit", "collect", "grab"
            ],
            "rhythm": [
                "节奏", "音乐", "方块", "拍子", "节拍", "韵律",
                "rhythm", "music", "beat", "tap"
            ],
            "maze": [
                "迷宫", "逃脱", "走出", "地牢", "洞穴",
                "maze", "escape", "dungeon", "navigate"
            ],
            "platformer": [
                "平台", "跳跃", "弹跳", "跳台", "跨越",
                "platform", "jump", "bouncing", "leap"
            ],
        }

        scores = {}
        for game_type, keywords in patterns.items():
            score = sum(1 for keyword in keywords if keyword in description_lower)
            scores[game_type] = score

        # Find game type with highest score
        if max(scores.values()) == 0:
            return {"game_type": "dodge", "confidence": 0.3}

        best_type = max(scores, key=scores.get)
        confidence = scores[best_type] / sum(scores.values())

        return {
            "game_type": best_type,
            "confidence": confidence,
            "scores": scores,
        }


class TestIntentParser:
    @pytest.fixture
    def parser(self):
        return IntentParser()

    def test_parse_dodge_chinese(self, parser):
        """Test parsing Chinese dodge game description"""
        result = parser.parse("太空飞船躲避陨石")
        assert result["game_type"] == "dodge"
        assert result["confidence"] > 0.5

    def test_parse_catcher_chinese(self, parser):
        """Test parsing Chinese catcher game description"""
        result = parser.parse("接水果的休闲游戏")
        assert result["game_type"] == "catcher"
        assert result["confidence"] > 0.4

    def test_parse_rhythm_chinese(self, parser):
        """Test parsing Chinese rhythm game description"""
        result = parser.parse("节奏方块音乐游戏")
        assert result["game_type"] == "rhythm"
        assert result["confidence"] > 0.4

    def test_parse_maze_chinese(self, parser):
        """Test parsing Chinese maze game description"""
        result = parser.parse("迷宫逃脱限时")
        assert result["game_type"] == "maze"
        assert result["confidence"] > 0.5

    def test_parse_platformer_chinese(self, parser):
        """Test parsing Chinese platformer description"""
        result = parser.parse("平台跳跃弹跳")
        assert result["game_type"] == "platformer"
        assert result["confidence"] > 0.5

    def test_parse_unknown_description(self, parser):
        """Test parsing unknown description returns reasonable default"""
        result = parser.parse("随机游戏描述")
        assert "game_type" in result
        assert result["game_type"] in [
            "dodge", "catcher", "rhythm", "maze", "platformer"
        ]
        assert "confidence" in result
        assert 0 <= result["confidence"] <= 1

    def test_parse_english_dodge(self, parser):
        """Test parsing English dodge description"""
        result = parser.parse("Avoid asteroids in space")
        assert result["game_type"] == "dodge"

    def test_parse_english_catcher(self, parser):
        """Test parsing English catcher description"""
        result = parser.parse("Catch falling fruits")
        assert result["game_type"] == "catcher"

    def test_parse_mixed_language(self, parser):
        """Test parsing mixed language description"""
        result = parser.parse("躲避 asteroids dodge game")
        assert result["game_type"] == "dodge"

    def test_parse_empty_string(self, parser):
        """Test parsing empty string"""
        result = parser.parse("")
        assert "game_type" in result
        assert result["confidence"] < 0.5

    def test_parse_none_input(self, parser):
        """Test parsing None input"""
        result = parser.parse(None)
        assert "game_type" in result
        assert result["confidence"] < 0.5

    def test_parse_returns_scores(self, parser):
        """Test that parse returns scores dict"""
        result = parser.parse("接水果的休闲游戏")
        assert "scores" in result
        assert isinstance(result["scores"], dict)

    def test_parse_confidence_range(self, parser):
        """Test that confidence is always between 0 and 1"""
        test_cases = [
            "太空飞船躲避陨石",
            "接水果",
            "节奏方块",
            "迷宫逃脱",
            "平台跳跃",
            "随机描述文本",
        ]

        for description in test_cases:
            result = parser.parse(description)
            assert 0 <= result["confidence"] <= 1
