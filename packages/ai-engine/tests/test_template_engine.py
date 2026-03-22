import pytest


class TemplateEngine:
    """Matches and generates game code from templates"""

    TEMPLATES = {
        "dodge": """
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <style>
        body {{ margin: 0; overflow: hidden; }}
        canvas {{ display: block; }}
    </style>
</head>
<body>
    <canvas id="gameCanvas"></canvas>
    <script>
        const canvas = document.getElementById('gameCanvas');
        const ctx = canvas.getContext('2d');
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;

        const player = {{ x: canvas.width / 2, y: canvas.height - 50, width: 50, height: 50 }};
        let obstacles = [];
        let score = 0;

        function gameLoop() {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            // Draw player
            ctx.fillStyle = '#00FF00';
            ctx.fillRect(player.x, player.y, player.width, player.height);
            
            // Draw obstacles and check collisions
            for (let obs of obstacles) {{
                ctx.fillStyle = '#FF0000';
                ctx.fillRect(obs.x, obs.y, obs.width, obs.height);
                obs.y += obs.speed;
                
                if (checkCollision(player, obs)) {{
                    alert('Game Over! Score: ' + score);
                    location.reload();
                }}
            }}
            
            // Remove off-screen obstacles
            obstacles = obstacles.filter(obs => obs.y < canvas.height);
            
            // Draw score
            ctx.fillStyle = '#FFFFFF';
            ctx.font = '20px Arial';
            ctx.fillText('Score: ' + score, 10, 30);
            
            requestAnimationFrame(gameLoop);
        }}

        function checkCollision(rect1, rect2) {{
            return rect1.x < rect2.x + rect2.width &&
                   rect1.x + rect1.width > rect2.x &&
                   rect1.y < rect2.y + rect2.height &&
                   rect1.y + rect1.height > rect2.y;
        }}

        // Controls
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'ArrowLeft') player.x = Math.max(0, player.x - 10);
            if (e.key === 'ArrowRight') player.x = Math.min(canvas.width - player.width, player.x + 10);
        }});

        gameLoop();
    </script>
</body>
</html>
        """,
        "catcher": """
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
</head>
<body>
    <canvas id="gameCanvas" width="800" height="600"></canvas>
    <script>
        const canvas = document.getElementById('gameCanvas');
        const ctx = canvas.getContext('2d');
        
        const basket = {{ x: canvas.width / 2 - 25, y: canvas.height - 60, width: 50, height: 30 }};
        let items = [];
        let score = 0;
        
        function gameLoop() {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#8B4513';
            ctx.fillRect(basket.x, basket.y, basket.width, basket.height);
            
            // Spawn items
            if (Math.random() < 0.02) {{
                items.push({{ x: Math.random() * canvas.width, y: 0, size: 20, speed: 2 }});
            }}
            
            for (let item of items) {{
                ctx.fillStyle = '#FFD700';
                ctx.beginPath();
                ctx.arc(item.x, item.y, item.size, 0, Math.PI * 2);
                ctx.fill();
                item.y += item.speed;
                
                if (item.x > basket.x && item.x < basket.x + basket.width &&
                    item.y > basket.y && item.y < canvas.height) {{
                    score++;
                    items = items.filter(i => i !== item);
                }}
            }}
            
            items = items.filter(item => item.y < canvas.height);
            ctx.fillStyle = '#FFFFFF';
            ctx.font = '20px Arial';
            ctx.fillText('Score: ' + score, 10, 30);
            
            requestAnimationFrame(gameLoop);
        }}
        
        document.addEventListener('mousemove', (e) => {{
            basket.x = e.clientX - basket.width / 2;
        }});
        
        gameLoop();
    </script>
</body>
</html>
        """,
        "rhythm": """
<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
</head>
<body>
    <canvas id="gameCanvas" width="800" height="600"></canvas>
    <script>
        const canvas = document.getElementById('gameCanvas');
        const ctx = canvas.getContext('2d');
        const gameType = 'rhythm';
        
        let notes = [];
        let score = 0;
        let combo = 0;
        
        // Generate notes
        for (let i = 0; i < 10; i++) {{
            notes.push({{ x: 100 + i * 70, y: 0, time: i * 500, width: 50, height: 20 }});
        }}
        
        let startTime = Date.now();
        
        function gameLoop() {{
            ctx.fillStyle = '#000000';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            
            const elapsed = Date.now() - startTime;
            
            for (let note of notes) {{
                const y = (elapsed - note.time) / 100;
                
                if (y > -5 && y < canvas.height + 5) {{
                    ctx.fillStyle = '#FF00FF';
                    ctx.fillRect(note.x, note.y + y, note.width, note.height);
                }}
            }}
            
            ctx.fillStyle = '#00FF00';
            ctx.fillRect(50, canvas.height - 50, canvas.width - 100, 5);
            
            ctx.fillStyle = '#FFFFFF';
            ctx.font = '20px Arial';
            ctx.fillText('Score: ' + score + ' | Combo: ' + combo, 10, 30);
            
            requestAnimationFrame(gameLoop);
        }}
        
        document.addEventListener('keydown', (e) => {{
            const key = e.key;
            // Check if key hits a note
            if (['a', 's', 'd', 'f'].includes(key)) {{
                score += 10;
                combo++;
            }}
        }});
        
        gameLoop();
    </script>
</body>
</html>
        """,
    }

    def match(self, game_type: str) -> bool:
        """Check if template exists for game type"""
        return game_type in self.TEMPLATES

    def generate(self, game_type: str, title: str) -> str:
        """Generate code from template with parameter substitution"""
        if game_type not in self.TEMPLATES:
            return ""

        template = self.TEMPLATES[game_type]
        return template.format(title=title)


class TestTemplateEngine:
    @pytest.fixture
    def engine(self):
        return TemplateEngine()

    def test_match_dodge(self, engine):
        """Test that dodge template exists"""
        assert engine.match("dodge")

    def test_match_catcher(self, engine):
        """Test that catcher template exists"""
        assert engine.match("catcher")

    def test_match_rhythm(self, engine):
        """Test that rhythm template exists"""
        assert engine.match("rhythm")

    def test_match_unknown(self, engine):
        """Test that unknown template doesn't exist"""
        assert not engine.match("unknown_type")

    def test_generate_dodge(self, engine):
        """Test generating dodge game code"""
        code = engine.generate("dodge", "Space Dodge")
        assert code != ""
        assert "gameCanvas" in code
        assert "Space Dodge" in code
        assert "dodge" in code.lower()

    def test_generate_catcher(self, engine):
        """Test generating catcher game code"""
        code = engine.generate("catcher", "Fruit Catcher")
        assert code != ""
        assert "gameCanvas" in code
        assert "Fruit Catcher" in code
        assert "basket" in code.lower()

    def test_generate_rhythm(self, engine):
        """Test generating rhythm game code"""
        code = engine.generate("rhythm", "Beat Master")
        assert code != ""
        assert "gameCanvas" in code
        assert "Beat Master" in code
        assert "rhythm" in code.lower()

    def test_generate_unknown_returns_empty(self, engine):
        """Test generating unknown game type returns empty string"""
        code = engine.generate("unknown_type", "Test Game")
        assert code == ""

    def test_generate_parameter_substitution(self, engine):
        """Test that title parameter is correctly substituted"""
        title = "My Custom Game"
        code = engine.generate("dodge", title)
        assert title in code

    def test_generate_html_structure(self, engine):
        """Test that generated code has valid HTML structure"""
        code = engine.generate("dodge", "Test")
        assert "<!DOCTYPE html>" in code
        assert "<canvas" in code
        assert "<script>" in code
        assert "gameLoop" in code

    def test_generate_contains_collision_detection(self, engine):
        """Test that dodge template includes collision detection"""
        code = engine.generate("dodge", "Test")
        assert "checkCollision" in code

    def test_generate_different_titles(self, engine):
        """Test generating with different titles produces unique code"""
        code1 = engine.generate("dodge", "Game 1")
        code2 = engine.generate("dodge", "Game 2")
        
        assert code1 != code2
        assert "Game 1" in code1
        assert "Game 2" in code2
