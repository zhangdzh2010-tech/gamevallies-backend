import pytest


class CodeGenerator:
    """Main code generator that combines intent parsing and fixture-based code generation"""

    def __init__(self):
        self.intent_parser = IntentParser()
        self.fixture_code_source = FixtureCodeSource()
        self.qa_pipeline = QAPipeline()

    def generate(self, description: str, title: str) -> dict:
        """Generate game code from description"""
        # Parse intent
        intent = self.intent_parser.parse(description)
        game_type = intent["game_type"]

        # Generate code
        code = self.fixture_code_source.generate(game_type, title)

        if not code:
            return {"success": False, "error": "No fixture for game type"}

        # Validate
        validation = self.qa_pipeline.validate_all(code)

        if not validation["passes_all"]:
            return {
                "success": False,
                "error": "Code validation failed",
                "validation": validation,
            }

        return {
            "success": True,
            "game_type": game_type,
            "confidence": intent["confidence"],
            "code": code,
            "title": title,
            "validation": validation,
        }

    def generate_with_feedback(self, code: str, feedback: str) -> dict:
        """Modify generated code based on feedback"""
        modifications = {
            "speed": self._modify_speed,
            "difficulty": self._modify_difficulty,
            "colors": self._modify_colors,
            "controls": self._modify_controls,
        }
        trigger_map = {
            "speed": ("speed", "faster", "slower"),
            "difficulty": ("difficulty", "easier", "harder"),
            "colors": ("colors", "color", "dark", "darker", "bright"),
            "controls": ("controls", "touch"),
        }

        modified_code = code
        normalized_feedback = feedback.lower()
        applied_modifications = []
        for key, modifier in modifications.items():
            if any(trigger in normalized_feedback for trigger in trigger_map[key]):
                modified_code = modifier(modified_code, feedback)
                applied_modifications.append(key)

        return {
            "original_code": code,
            "modified_code": modified_code,
            "modifications": applied_modifications,
        }

    def _modify_speed(self, code: str, feedback: str) -> str:
        """Modify game speed based on feedback"""
        if "faster" in feedback.lower():
            code = code.replace("speed: 2", "speed: 3")
            code = code.replace("speed: 3", "speed: 4")
        elif "slower" in feedback.lower():
            code = code.replace("speed: 3", "speed: 2")
            code = code.replace("speed: 4", "speed: 3")
        return code

    def _modify_difficulty(self, code: str, feedback: str) -> str:
        """Modify game difficulty"""
        if "easier" in feedback.lower():
            code = code.replace("0.02", "0.01")
        elif "harder" in feedback.lower():
            code = code.replace("0.01", "0.03")
        return code

    def _modify_colors(self, code: str, feedback: str) -> str:
        """Modify game colors"""
        if "dark" in feedback.lower():
            code = code.replace("#FFFFFF", "#333333")
        elif "bright" in feedback.lower():
            code = code.replace("#000000", "#FFFFFF")
        return code

    def _modify_controls(self, code: str, feedback: str) -> str:
        """Modify game controls"""
        if "touch" in feedback.lower():
            code = code.replace(
                "document.addEventListener('keydown'",
                "document.addEventListener('touchmove'",
            )
        return code


class IntentParser:
    def parse(self, description: str) -> dict:
        # Simplified for test
        return {"game_type": "dodge", "confidence": 0.8}


class FixtureCodeSource:
    def generate(self, game_type: str, title: str) -> str:
        if game_type == "dodge":
            return f"""
<!DOCTYPE html>
<html>
<head><title>{title}</title></head>
<body>
<canvas id="gameCanvas"></canvas>
<script>
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');

let speed = 2;

function gameLoop() {{
  ctx.clearRect(0, 0, 800, 600);
  requestAnimationFrame(gameLoop);
}}

document.addEventListener('keydown', (e) => {{
  console.log('Key: ' + e.key);
}});

gameLoop();
</script>
</body>
</html>
            """
        return ""


class QAPipeline:
    def validate_all(self, code: str) -> dict:
        return {
            "passes_all": True,
            "syntax": {"valid": True},
            "security": {"secure": True},
            "size": {"valid": True},
            "structure": {"valid": True},
        }


class TestCodeGenerator:
    @pytest.fixture
    def generator(self):
        return CodeGenerator()

    def test_generate_returns_dict(self, generator):
        """Test that generate returns a dictionary"""
        result = generator.generate("Space dodge game", "Space Game")
        assert isinstance(result, dict)
        assert "success" in result

    def test_generate_success(self, generator):
        """Test successful code generation"""
        result = generator.generate("Space dodge game", "Space Game")
        assert result["success"]
        assert "code" in result
        assert "game_type" in result
        assert "confidence" in result

    def test_generate_includes_title(self, generator):
        """Test that generated code includes title"""
        title = "My Custom Game"
        result = generator.generate("dodge game", title)
        if result["success"]:
            assert title in result["code"]

    def test_generate_includes_game_type(self, generator):
        """Test that result includes detected game type"""
        result = generator.generate("dodge game", "Test")
        if result["success"]:
            assert "game_type" in result
            assert result["game_type"] in [
                "dodge", "catcher", "rhythm", "maze", "platformer"
            ]

    def test_generate_includes_validation(self, generator):
        """Test that result includes validation info"""
        result = generator.generate("dodge game", "Test")
        if result["success"]:
            assert "validation" in result
            assert "syntax" in result["validation"]
            assert "security" in result["validation"]

    def test_generate_includes_confidence(self, generator):
        """Test that result includes confidence score"""
        result = generator.generate("dodge game", "Test")
        if result["success"]:
            assert "confidence" in result
            assert 0 <= result["confidence"] <= 1

    def test_generate_with_feedback_modifies_speed(self, generator):
        """Test that feedback can modify game speed"""
        original_code = "speed: 2"
        feedback = "faster"

        result = generator.generate_with_feedback(original_code, feedback)

        assert "modified_code" in result
        assert result["original_code"] == original_code
        assert "speed" in result["modifications"]

    def test_generate_with_feedback_modifies_difficulty(self, generator):
        """Test that feedback can modify difficulty"""
        original_code = "spawn_rate: 0.02"
        feedback = "harder"

        result = generator.generate_with_feedback(original_code, feedback)

        assert "modified_code" in result
        assert "difficulty" in result["modifications"]

    def test_generate_with_feedback_modifies_colors(self, generator):
        """Test that feedback can modify colors"""
        original_code = "#FFFFFF"
        feedback = "dark theme"

        result = generator.generate_with_feedback(original_code, feedback)

        assert "colors" in result["modifications"]

    def test_generate_with_feedback_preserves_original(self, generator):
        """Test that original code is preserved in result"""
        original = "test code"
        result = generator.generate_with_feedback(original, "faster")

        assert result["original_code"] == original

    def test_generate_with_feedback_returns_modified(self, generator):
        """Test that modified code is returned"""
        original = "speed: 2 code"
        result = generator.generate_with_feedback(original, "faster")

        assert "modified_code" in result
        assert isinstance(result["modified_code"], str)

    def test_generate_with_feedback_multiple_modifications(self, generator):
        """Test multiple modifications in one feedback"""
        original_code = "speed: 2 color #FFFFFF"
        feedback = "faster and darker"

        result = generator.generate_with_feedback(original_code, feedback)

        # At least one modification should be detected
        assert len(result["modifications"]) > 0

    def test_generate_handles_invalid_game_type(self, generator):
        """Test handling of unrecognized game description"""
        result = generator.generate("zzzzzz random text", "Test")

        # Should either fail gracefully or return a default
        assert isinstance(result, dict)
        assert "success" in result

    def test_generate_code_contains_canvas(self, generator):
        """Test that generated code contains canvas"""
        result = generator.generate("dodge", "Test")
        if result["success"]:
            assert "canvas" in result["code"].lower()

    def test_generate_code_is_html(self, generator):
        """Test that generated code is valid HTML"""
        result = generator.generate("dodge", "Test")
        if result["success"]:
            assert "<!DOCTYPE" in result["code"]
            assert "<script>" in result["code"]
