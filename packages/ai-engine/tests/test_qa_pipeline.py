import pytest


class QAPipeline:
    """Quality assurance pipeline for generated game code"""

    def validate_syntax(self, html_code: str) -> dict:
        """Validate HTML/JavaScript syntax"""
        issues = []

        if not html_code:
            return {"valid": False, "issues": ["Empty code"]}

        # Check basic structure
        if "<!DOCTYPE" not in html_code:
            issues.append("Missing DOCTYPE declaration")

        if "<html>" not in html_code:
            issues.append("Missing <html> tag")

        if "<canvas" not in html_code:
            issues.append("Missing canvas element")

        if "<script>" not in html_code:
            issues.append("Missing script tag")

        if "gameLoop" not in html_code:
            issues.append("Missing game loop function")

        # Basic bracket matching
        if html_code.count("{") != html_code.count("}"):
            issues.append("Mismatched braces")

        if html_code.count("[") != html_code.count("]"):
            issues.append("Mismatched brackets")

        if html_code.count("(") != html_code.count(")"):
            issues.append("Mismatched parentheses")

        return {"valid": len(issues) == 0, "issues": issues}

    def validate_security(self, html_code: str) -> dict:
        """Validate code for security issues"""
        issues = []

        # Check for eval
        if "eval(" in html_code:
            issues.append("Code contains eval() - security risk")

        # Check for fetch
        if "fetch(" in html_code:
            issues.append("Code contains fetch() - verify API calls")

        # Check for localStorage
        if "localStorage" in html_code:
            issues.append("Code contains localStorage - verify data handling")

        # Check for XMLHttpRequest
        if "XMLHttpRequest" in html_code:
            issues.append("Code contains XMLHttpRequest - verify API calls")

        return {
            "secure": len(issues) == 0,
            "issues": issues,
            "warnings": len(issues),
        }

    def validate_size(self, html_code: str, max_size_kb: int = 500) -> dict:
        """Validate code size"""
        size_kb = len(html_code) / 1024

        return {
            "size_kb": round(size_kb, 2),
            "max_size_kb": max_size_kb,
            "valid": size_kb <= max_size_kb,
            "issue": f"Code exceeds {max_size_kb}KB limit"
            if size_kb > max_size_kb
            else None,
        }

    def validate_structure(self, html_code: str) -> dict:
        """Validate game code structure"""
        issues = []

        if "const canvas = document.getElementById" not in html_code:
            issues.append("Missing canvas initialization")

        if "getContext('2d')" not in html_code:
            issues.append("Missing 2D context")

        if "requestAnimationFrame" not in html_code:
            issues.append("Missing animation frame request")

        if (
            "clearRect" not in html_code and "fillRect" not in html_code
        ) and "arc" not in html_code:
            issues.append("Missing drawing functions")

        if "addEventListener" not in html_code:
            issues.append("Missing event listeners")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "structure_checks": 5,
        }

    def validate_all(self, html_code: str) -> dict:
        """Run all validations"""
        return {
            "syntax": self.validate_syntax(html_code),
            "security": self.validate_security(html_code),
            "size": self.validate_size(html_code),
            "structure": self.validate_structure(html_code),
            "passes_all": (
                self.validate_syntax(html_code)["valid"]
                and self.validate_security(html_code)["secure"]
                and self.validate_size(html_code)["valid"]
                and self.validate_structure(html_code)["valid"]
            ),
        }


class TestQAPipeline:
    @pytest.fixture
    def qa(self):
        return QAPipeline()

    @pytest.fixture
    def valid_game_code(self):
        return """
<!DOCTYPE html>
<html>
<head><title>Game</title></head>
<body>
<canvas id="gameCanvas"></canvas>
<script>
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');

function gameLoop() {
  ctx.clearRect(0, 0, 800, 600);
  requestAnimationFrame(gameLoop);
}

document.addEventListener('keydown', (e) => {
  console.log('Key pressed');
});

gameLoop();
</script>
</body>
</html>
        """

    def test_validate_syntax_valid(self, qa, valid_game_code):
        """Test valid HTML syntax passes validation"""
        result = qa.validate_syntax(valid_game_code)
        assert result["valid"]
        assert len(result["issues"]) == 0

    def test_validate_syntax_missing_doctype(self, qa):
        """Test missing DOCTYPE is caught"""
        code = "<html><body><canvas></canvas></body></html>"
        result = qa.validate_syntax(code)
        assert not result["valid"]
        assert any("DOCTYPE" in issue for issue in result["issues"])

    def test_validate_syntax_missing_canvas(self, qa):
        """Test missing canvas is caught"""
        code = """
<!DOCTYPE html>
<html>
<script>function gameLoop() {}</script>
</html>
        """
        result = qa.validate_syntax(code)
        assert not result["valid"]
        assert any("canvas" in issue.lower() for issue in result["issues"])

    def test_validate_syntax_mismatched_braces(self, qa):
        """Test mismatched braces are caught"""
        code = """
<!DOCTYPE html>
<html>
<canvas></canvas>
<script>
function test() { if (true) { console.log('test'); }
</script>
</html>
        """
        result = qa.validate_syntax(code)
        assert not result["valid"]

    def test_validate_security_no_issues(self, qa, valid_game_code):
        """Test secure code passes security validation"""
        result = qa.validate_security(valid_game_code)
        assert result["secure"]
        assert result["warnings"] == 0

    def test_validate_security_detects_eval(self, qa):
        """Test detection of eval() in code"""
        code = """
<script>
eval('malicious code');
</script>
        """
        result = qa.validate_security(code)
        assert not result["secure"]
        assert any("eval" in issue.lower() for issue in result["issues"])

    def test_validate_security_detects_fetch(self, qa):
        """Test detection of fetch() calls"""
        code = """
<script>
fetch('/api/data');
</script>
        """
        result = qa.validate_security(code)
        assert any("fetch" in issue.lower() for issue in result["issues"])

    def test_validate_security_detects_localStorage(self, qa):
        """Test detection of localStorage usage"""
        code = """
<script>
localStorage.setItem('data', 'value');
</script>
        """
        result = qa.validate_security(code)
        assert any("localStorage" in issue for issue in result["issues"])

    def test_validate_size_under_limit(self, qa, valid_game_code):
        """Test code size validation for code under limit"""
        result = qa.validate_size(valid_game_code)
        assert result["valid"]
        assert result["size_kb"] <= result["max_size_kb"]

    def test_validate_size_over_limit(self, qa):
        """Test code size validation for code over limit"""
        large_code = "x" * (501 * 1024)  # 501 KB
        result = qa.validate_size(large_code, max_size_kb=500)
        assert not result["valid"]
        assert result["size_kb"] > 500

    def test_validate_structure_valid(self, qa, valid_game_code):
        """Test valid game structure passes validation"""
        result = qa.validate_structure(valid_game_code)
        assert result["valid"]
        assert len(result["issues"]) == 0

    def test_validate_structure_missing_canvas_init(self, qa):
        """Test missing canvas initialization is detected"""
        code = """
<script>
function gameLoop() {
  requestAnimationFrame(gameLoop);
}
gameLoop();
</script>
        """
        result = qa.validate_structure(code)
        assert not result["valid"]

    def test_validate_structure_missing_gameloop(self, qa):
        """Test missing game loop is detected"""
        code = """
<canvas id="gameCanvas"></canvas>
<script>
const canvas = document.getElementById('gameCanvas');
</script>
        """
        result = qa.validate_structure(code)
        assert not result["valid"]

    def test_validate_all_comprehensive(self, qa, valid_game_code):
        """Test comprehensive validation"""
        result = qa.validate_all(valid_game_code)
        assert result["passes_all"]
        assert result["syntax"]["valid"]
        assert result["security"]["secure"]
        assert result["size"]["valid"]
        assert result["structure"]["valid"]

    def test_validate_all_with_security_issue(self, qa):
        """Test validation fails with security issue"""
        code = """
<!DOCTYPE html>
<html>
<canvas id="gameCanvas"></canvas>
<script>
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
function gameLoop() {
  eval('alert(1)');
  ctx.clearRect(0, 0, 800, 600);
  requestAnimationFrame(gameLoop);
}
document.addEventListener('keydown', (e) => {});
gameLoop();
</script>
</html>
        """
        result = qa.validate_all(code)
        assert not result["passes_all"]
        assert not result["security"]["secure"]
