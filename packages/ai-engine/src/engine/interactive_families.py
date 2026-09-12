"""L1 interaction families and L2 subject×family recipes.

Coverage is by subject × interaction-family, not an exhaustive experiment list.
Templates stay skeleton-only: each family supplies a model step/draw plugin
that the DesktopRuntimeShell hosts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SUBJECTS = ("physics", "chem", "bio")
FAMILY_IDS = (
    "param_formula_panel",
    "time_integrator_1d",
    "field_or_wave_2d",
    "compartment_flow",
    "geometric_ray_2d",
)
# Arcade path is out of scope; stub so the registry has a stable slot.
GAME_FAMILY_STUBS = ("arcade_loop",)


@dataclass(frozen=True)
class Recipe:
    id: str
    family_id: str
    subject: str
    title: str
    formula: str
    keywords: Tuple[str, ...]
    params: Tuple[Dict[str, Any], ...]
    assumptions: str
    limits: str
    units: Dict[str, str] = field(default_factory=dict)
    required_any: Tuple[str, ...] = ()

    def keyword_hits(self, blob: str) -> int:
        if self.required_any and not any(token in blob for token in self.required_any):
            return 0
        return sum(1 for key in self.keywords if key and key in blob)


@dataclass(frozen=True)
class Family:
    id: str
    label: str
    keywords: Tuple[str, ...]
    subjects: Tuple[str, ...]
    interaction: str

    def keyword_hits(self, blob: str) -> int:
        return sum(1 for key in self.keywords if key and key in blob)


def _param(name: str, label: str, minimum: float, maximum: float, step: float, value: float, unit: str = "") -> Dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "min": minimum,
        "max": maximum,
        "step": step,
        "value": value,
        "unit": unit,
    }


FAMILIES: Dict[str, Family] = {
    "param_formula_panel": Family(
        id="param_formula_panel",
        label="参数-公式面板",
        keywords=(
            "定律", "公式", "ohm", "欧姆", "电压", "电阻", "电流",
            "理想气体", "gas law", "pv=nrt", "压强", "体积",
            "酶", "enzyme", "温度", "反应速率", "arrhenius",
            "f=ma", "牛顿", "光合", "photosynthesis", "光照", "二氧化碳",
        ),
        subjects=("physics", "chem", "bio"),
        interaction="live formula from labeled parameters",
    ),
    "time_integrator_1d": Family(
        id="time_integrator_1d",
        label="一维时间积分",
        keywords=(
            "单摆", "pendulum", "摆长", "周期", "自由落体", "free fall",
            "下落", "重力加速度", "抛体", "一维运动", "积分",
        ),
        subjects=("physics",),
        interaction="fixed-step 1d time integration",
    ),
    "field_or_wave_2d": Family(
        id="field_or_wave_2d",
        label="二维场或波动",
        keywords=(
            "波", "干涉", "interference", "波长", "振幅", "波源",
            "波动", "场", "wave", "衍射",
        ),
        subjects=("physics",),
        interaction="2d field or superposed waves",
    ),
    "compartment_flow": Family(
        id="compartment_flow",
        label="隔室流动",
        keywords=(
            "渗透", "osmosis", "隔室", "种群", "捕食", "猎物",
            "lotka", "volterra", "流动", "扩散", "compartment",
        ),
        subjects=("bio", "chem"),
        interaction="exchange or coupled compartment rates",
    ),
    "geometric_ray_2d": Family(
        id="geometric_ray_2d",
        label="几何光线",
        keywords=(
            "平面镜", "反射定律", "入射角", "反射角", "法线",
            "镜面反射", "plane mirror", "reflection law", "geometric optics",
        ),
        subjects=("physics",),
        interaction="labeled angle drives incident and reflected rays",
    ),
}


RECIPES: Dict[str, Recipe] = {
    "ohm_law": Recipe(
        id="ohm_law",
        family_id="param_formula_panel",
        subject="physics",
        title="欧姆定律",
        formula="I = V / R",
        keywords=("欧姆", "ohm", "电压", "电阻", "电流", "i=v/r", "v/r"),
        params=(
            _param("V", "电压 V", 1, 24, 0.5, 12, "V"),
            _param("R", "电阻 R", 1, 20, 0.5, 6, "Ω"),
        ),
        assumptions="理想电阻，直流，温度不变。",
        limits="不包含电容、电感或非线性元件。",
        units={"V": "V", "R": "Ω", "I": "A"},
        required_any=("欧姆", "ohm", "i=v/r", "v/r", "电阻"),
    ),
    "gas_law": Recipe(
        id="gas_law",
        family_id="param_formula_panel",
        subject="chem",
        title="理想气体状态方程",
        formula="PV = nRT",
        keywords=("理想气体", "gas law", "pv=nrt", "压强", "体积", "摩尔", "气体定律"),
        params=(
            _param("n", "物质的量 n", 0.2, 3, 0.1, 1, "mol"),
            _param("T", "温度 T", 200, 400, 5, 298, "K"),
            _param("V", "体积 V", 0.01, 0.05, 0.001, 0.024, "m³"),
        ),
        assumptions="理想气体，平衡态，R=8.314 J/(mol·K)。",
        limits="高压、低温或真实气体偏差未建模。",
        units={"P": "Pa", "V": "m³", "n": "mol", "T": "K"},
        required_any=("理想气体", "gas law", "pv=nrt", "气体定律", "气体状态"),
    ),
    "enzyme_temp": Recipe(
        id="enzyme_temp",
        family_id="param_formula_panel",
        subject="bio",
        title="酶活性-温度曲线",
        formula="rate = k·exp(-Ea/RT)·D(T)",
        keywords=("酶", "enzyme", "温度", "活性", "变性", "arrhenius", "反应速率"),
        params=(
            _param("T", "温度 T", 0, 80, 1, 37, "°C"),
            _param("Ea", "活化能 Ea", 20, 80, 1, 50, "kJ/mol"),
        ),
        assumptions="简化 Arrhenius 乘以热变性项，不是实验测得的酶活。",
        limits="未区分底物饱和或 pH；示意曲线。",
        units={"T": "°C", "rate": "a.u."},
        required_any=("酶", "enzyme", "arrhenius", "酶活"),
    ),
    "photosynthesis_rate": Recipe(
        id="photosynthesis_rate",
        family_id="param_formula_panel",
        subject="bio",
        title="光合产氧速率",
        formula="rate ∝ I·C / (kI + I) / (kC + C)",
        keywords=("光合", "photosynthesis", "光照", "二氧化碳", "氧气", "产氧"),
        params=(
            _param("I", "光照 I", 0, 100, 1, 40, "%"),
            _param("C", "CO₂ 浓度", 100, 800, 10, 400, "ppm"),
        ),
        assumptions="饱和型示意速率，不是叶片气体交换数据。",
        limits="未建模光抑制、气孔或温度。",
        units={"I": "%", "C": "ppm", "rate": "a.u."},
        required_any=("光合", "photosynthesis", "产氧"),
    ),
    "newtons_second": Recipe(
        id="newtons_second",
        family_id="param_formula_panel",
        subject="physics",
        title="牛顿第二定律",
        formula="a = F / m",
        keywords=("牛顿", "第二定律", "f=ma", "质量", "加速度", "力"),
        params=(
            _param("F", "力 F", 1, 20, 0.5, 10, "N"),
            _param("m", "质量 m", 0.5, 10, 0.5, 2, "kg"),
        ),
        assumptions="质点、合力恒定、无摩擦。",
        limits="不包含转动或变质量。",
        units={"F": "N", "m": "kg", "a": "m/s²"},
        required_any=("牛顿", "f=ma", "第二定律"),
    ),
    "pendulum": Recipe(
        id="pendulum",
        family_id="time_integrator_1d",
        subject="physics",
        title="小角度理想单摆",
        formula="θ'' = -(g/L) sin θ，  T=2π√(L/g)",
        keywords=("单摆", "pendulum", "摆长", "周期", "小角度", "θ", "摆球"),
        params=(
            _param("L", "摆长 L", 0.5, 2.0, 0.1, 1.0, "m"),
            _param("g", "重力 g", 5, 15, 0.1, 9.8, "m/s²"),
            _param("theta0", "初角 θ₀", 0.15, 0.6, 0.05, 0.35, "rad"),
        ),
        assumptions="无阻尼单摆；周期读数用小角度公式。",
        limits="大角度时小角度周期不再准确。",
        units={"L": "m", "g": "m/s²", "T": "s"},
        required_any=("单摆", "pendulum", "摆球", "摆长"),
    ),
    "free_fall": Recipe(
        id="free_fall",
        family_id="time_integrator_1d",
        subject="physics",
        title="自由落体",
        formula="v = gt，  y = y0 - ½gt²",
        keywords=("自由落体", "free fall", "下落", "高度", "v=gt", "重力加速度"),
        params=(
            _param("y0", "高度 y0", 5, 40, 1, 20, "m"),
            _param("g", "重力 g", 5, 15, 0.1, 9.8, "m/s²"),
        ),
        assumptions="无空气阻力，从静止释放。",
        limits="落地后停止积分，不反弹。",
        units={"y": "m", "v": "m/s", "g": "m/s²"},
        required_any=("自由落体", "free fall", "下落"),
    ),
    "wave_interference": Recipe(
        id="wave_interference",
        family_id="field_or_wave_2d",
        subject="physics",
        title="双波源干涉",
        formula="y = A₁sin(kx-ωt) + A₂sin(kx-ωt+φ)",
        keywords=("干涉", "interference", "双波", "波源", "波长", "振幅", "波形"),
        params=(
            _param("A1", "振幅 A1", 0.2, 1.2, 0.1, 0.6, ""),
            _param("A2", "振幅 A2", 0.2, 1.2, 0.1, 0.6, ""),
            _param("lambda", "波长 λ", 40, 140, 5, 80, "px"),
            _param("phi", "相位差 φ", 0.2, 6.2, 0.1, 1.2, "rad"),
        ),
        assumptions="一维投影的两列简谐波线性叠加。",
        limits="不是水槽实验数据；坐标按 2A 留边。",
        units={"A": "a.u.", "lambda": "px"},
        required_any=("干涉", "interference", "双波", "波形"),
    ),
    "mirror_optics": Recipe(
        id="mirror_optics",
        family_id="geometric_ray_2d",
        subject="physics",
        title="平面镜反射",
        formula="θᵢ = θᵣ",
        keywords=(
            "平面镜", "反射定律", "入射角", "入射光", "反射光",
            "镜面", "法线", "反射角", "plane mirror", "reflection",
        ),
        params=(
            _param("theta", "入射角 θ", 10, 70, 1, 30, "°"),
        ),
        assumptions="平面镜，法线垂直于镜面；入射光指向交点，反射光离开交点。",
        limits="几何光学示意，不考虑波动、吸收或镜厚。",
        units={"theta": "°"},
        required_any=("平面镜", "反射定律", "反射光", "plane mirror", "法线", "镜面反射"),
    ),
    "osmosis": Recipe(
        id="osmosis",
        family_id="compartment_flow",
        subject="bio",
        title="渗透水流",
        formula="J = k (c_in - c_out)",
        keywords=("渗透", "osmosis", "半透", "浓度", "隔室", "水流"),
        params=(
            _param("cin", "内侧浓度", 0.1, 1.2, 0.05, 0.8, "mol/L"),
            _param("cout", "外侧浓度", 0.0, 1.0, 0.05, 0.2, "mol/L"),
            _param("k", "渗透系数 k", 0.05, 0.4, 0.05, 0.15, "1/s"),
        ),
        assumptions="两侧充分混合，仅水透过示意膜。",
        limits="忽略静水压与真实细胞膜通道。",
        units={"c": "mol/L"},
        required_any=("渗透", "osmosis", "半透"),
    ),
    "population": Recipe(
        id="population",
        family_id="compartment_flow",
        subject="bio",
        title="捕食者-猎物种群",
        formula="x'=x(α-βy)，  y'=y(δx-γ)",
        keywords=("种群", "捕食", "猎物", "lotka", "volterra", "增长率", "捕食率"),
        params=(
            _param("alpha", "猎物增长 α", 0.4, 1.4, 0.1, 0.8, ""),
            _param("beta", "捕食率 β", 0.2, 1.0, 0.05, 0.5, ""),
            _param("gamma", "捕食者死亡 γ", 0.2, 1.0, 0.05, 0.4, ""),
        ),
        assumptions="经典 Lotka–Volterra，连续时间。",
        limits="示意曲线，不是野外计数。",
        units={"x": "prey", "y": "pred"},
        required_any=("种群", "捕食", "猎物", "lotka", "volterra"),
    ),
}


def get_family(family_id: str) -> Optional[Family]:
    return FAMILIES.get(family_id)


def get_recipe(recipe_id: str) -> Optional[Recipe]:
    return RECIPES.get(recipe_id)


def recipes_for_family(family_id: str) -> List[Recipe]:
    return [recipe for recipe in RECIPES.values() if recipe.family_id == family_id]


def iter_recipes(subject: Optional[str] = None) -> Iterable[Recipe]:
    for recipe in RECIPES.values():
        if subject and recipe.subject != subject:
            continue
        yield recipe


def normalize_brief(text: str) -> str:
    return (text or "").strip().lower()


def render_param_controls(params: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for spec in params:
        name = spec["name"]
        parts.append(
            '<label>{label}<input data-work-param="{name}" id="param-{name}" '
            'type="range" min="{min}" max="{max}" step="{step}" value="{value}">'
            '</label>'.format(
                label=spec["label"],
                name=name,
                min=spec["min"],
                max=spec["max"],
                step=spec["step"],
                value=spec["value"],
            )
        )
    return "".join(parts)


def family_plugin_js(family_id: str, recipe: Recipe) -> str:
    """Deterministic model plugin. Presentation copy is filled around it."""
    plugins = {
        "param_formula_panel": _PARAM_FORMULA_JS,
        "time_integrator_1d": _TIME_INTEGRATOR_JS,
        "field_or_wave_2d": _WAVE_JS,
        "compartment_flow": _COMPARTMENT_JS,
        "geometric_ray_2d": _RAY_OPTICS_JS,
    }
    template = plugins.get(family_id, _PARAM_FORMULA_JS)
    return template.replace("__RECIPE__", recipe.id)


def wave_superposition(x: float, t: float, *, amplitude1: float, amplitude2: float, wavelength: float, phase: float) -> float:
    """y = A1 sin(kx - t) + A2 sin(kx - t + φ). Phase is only on the second source."""
    lam = max(float(wavelength), 1e-6)
    k = 2.0 * math.pi / lam
    return (
        float(amplitude1) * math.sin(k * x - t)
        + float(amplitude2) * math.sin(k * x - t + float(phase))
    )


def plane_mirror_rays(theta_rad: float, *, hit: Tuple[float, float] = (0.0, 0.0), length: float = 1.0) -> Dict[str, Tuple[float, float]]:
    """Vertical mirror, normal along -x. Incident travels toward hit; reflected leaves hit."""
    mx, cy = hit
    length = max(float(length), 1e-6)
    theta = max(0.0, min(math.pi / 2 - 1e-3, float(theta_rad)))
    incident_from = (mx - length * math.cos(theta), cy - length * math.sin(theta))
    reflected_to = (mx - length * math.cos(theta), cy + length * math.sin(theta))
    return {
        "incident_from": incident_from,
        "hit": (mx, cy),
        "reflected_to": reflected_to,
        "normal_to": (mx - length, cy),
    }


_PARAM_FORMULA_JS = r"""
  window.WorkFamily = (function(){
    var recipe = "__RECIPE__";
    var phase = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function compute(){
      if (recipe === 'ohm_law'){
        var V = num('param-V', 12), R = Math.max(0.01, num('param-R', 6));
        var I = V / R;
        setReadout('I=' + I.toFixed(3) + ' A');
        return {kind:'ohm', V:V, R:R, I:I};
      }
      if (recipe === 'gas_law'){
        var n = num('param-n', 1), T = num('param-T', 298), vol = Math.max(0.001, num('param-V', 0.024));
        var P = n * 8.314 * T / vol;
        var nRT = n * 8.314 * T;
        setReadout('P=' + P.toFixed(0) + ' Pa  PV=' + (P*vol).toFixed(0) + '  nRT=' + nRT.toFixed(0));
        return {kind:'gas', n:n, T:T, V:vol, P:P};
      }
      if (recipe === 'enzyme_temp'){
        var Tc = num('param-T', 37), Ea = num('param-Ea', 50);
        var Tk = Tc + 273.15;
        var arr = Math.exp(-Ea * 1000 / (8.314 * Tk));
        var denature = 1 / (1 + Math.exp((Tc - 55) / 4));
        var rate = 4000 * arr * denature;
        setReadout('rate=' + rate.toFixed(3) + ' a.u.');
        return {kind:'enzyme', T:Tc, rate:rate};
      }
      if (recipe === 'photosynthesis_rate'){
        var I = num('param-I', 40), C = num('param-C', 400);
        var rate = (I / (20 + I)) * (C / (200 + C));
        setReadout('O₂ rate=' + rate.toFixed(3) + ' a.u.');
        return {kind:'photo', I:I, C:C, rate:rate};
      }
      var F = num('param-F', 10), m = Math.max(0.1, num('param-m', 2));
      var a = F / m;
      setReadout('a=' + a.toFixed(3) + ' m/s²');
      return {kind:'newton', F:F, m:m, a:a};
    }
    return {
      applyParams: compute,
      onStart: function(){ phase = 0.2; },
      reset: function(){ phase = 0.2; compute(); },
      step: function(dt){ phase += dt; },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        var s = compute();
        var w = canvas.width, h = canvas.height;
        ctx.clearRect(0,0,w,h);
        ctx.fillStyle = '#0f172a'; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = '#38bdf8'; ctx.lineWidth = 2;
        if (s.kind === 'ohm'){
          ctx.strokeRect(w*0.2, h*0.35, w*0.6, h*0.3);
          var pulse = 0.3 + 0.2 * Math.sin(phase * s.I);
          ctx.fillStyle = '#facc15';
          ctx.beginPath(); ctx.arc(w*(0.25+pulse), h*0.5, 8, 0, Math.PI*2); ctx.fill();
        } else if (s.kind === 'gas'){
          var vNorm = Math.min(1, Math.max(0, (s.V - 0.01) / 0.04));
          var chamberH = h * (0.32 + 0.48 * vNorm);
          var chamberW = w * 0.26;
          var left = w * 0.08;
          var top = h - 14 - chamberH;
          ctx.strokeStyle = '#94a3b8';
          ctx.strokeRect(left, top, chamberW, chamberH);
          ctx.fillStyle = '#64748b';
          ctx.fillRect(left - 6, top - 8, chamberW + 12, 10);
          var count = Math.max(5, Math.round(s.n * 8));
          var speed = Math.sqrt(Math.max(80, s.T) / 298);
          var heat = Math.min(1, (s.T - 200) / 200);
          ctx.fillStyle = 'rgb(' + Math.round(80+175*heat) + ',' + Math.round(180-80*heat) + ',80)';
          for (var i=0;i<count;i++){
            var px = left + 12 + (chamberW-24) * (0.5 + 0.46*Math.sin(phase*speed*3.1+i*1.7));
            var py = top + 18 + (chamberH-30) * (0.5 + 0.46*Math.cos(phase*speed*2.4+i*1.3));
            ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI*2); ctx.fill();
          }
          ctx.fillStyle = '#e2e8f0';
          ctx.font = '12px sans-serif';
          ctx.fillText('活塞体积 ∝ V', left + chamberW + 16, 28);
          ctx.fillText('P=' + s.P.toFixed(0) + ' Pa', left + chamberW + 16, 48);
        } else if (s.kind === 'enzyme' || s.kind === 'photo'){
          var y = h - 20 - (s.rate * (s.kind==='photo'? h*0.6 : Math.min(h*0.7, s.rate*40)));
          ctx.beginPath(); ctx.moveTo(20,h-20);
          for (var x=20;x<w-20;x+=6){
            var t = (x-20)/(w-40);
            var yy = s.kind==='photo' ? h-20 - s.rate*h*0.55*(0.4+0.6*t) : h-20 - Math.min(h*0.7, (0.3+t)*s.rate*30);
            ctx.lineTo(x, yy + 6*Math.sin(phase+t*6));
          }
          ctx.stroke();
          ctx.fillStyle = '#f97316'; ctx.fillRect(w*0.7, y, 10, 10);
        } else {
          var x = 40 + (w-80) * (0.15 + 0.35*(1-Math.cos(phase * Math.min(2, s.a/4))));
          ctx.fillStyle = '#22c55e'; ctx.fillRect(x, h*0.45, 24, 24);
        }
      }
    };
  })();
"""


_TIME_INTEGRATOR_JS = r"""
  window.WorkFamily = (function(){
    var recipe = "__RECIPE__";
    var theta = 0.35, omega = 0, y = 20, v = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function period(L,g){ return 2*Math.PI*Math.sqrt(Math.max(0.05,L)/Math.max(0.1,g)); }
    return {
      applyParams: function(){
        if (recipe === 'pendulum'){
          var L = num('param-L',1), g = num('param-g',9.8);
          setReadout('T=' + period(L,g).toFixed(3) + ' s');
        } else {
          var g2 = num('param-g',9.8), tHint = Math.sqrt(2*Math.max(0.1,y)/g2);
          setReadout('v=' + v.toFixed(2) + ' m/s');
          return tHint;
        }
      },
      onStart: function(){
        if (recipe === 'pendulum' && Math.abs(theta) < 0.05) theta = num('param-theta0', 0.35);
        if (recipe === 'free_fall' && y < 0.2) y = num('param-y0', 20);
      },
      reset: function(){
        if (recipe === 'pendulum'){ theta = num('param-theta0', 0.35); omega = 0; }
        else { y = num('param-y0', 20); v = 0; }
        this.applyParams();
      },
      step: function(dt){
        if (recipe === 'pendulum'){
          var L = Math.max(0.2, num('param-L',1)), g = num('param-g',9.8);
          omega += -(g/L) * Math.sin(theta) * dt;
          theta += omega * dt;
          setReadout('T=' + period(L,g).toFixed(3) + ' s  θ=' + theta.toFixed(3));
        } else {
          var g = num('param-g',9.8);
          if (y > 0){ v += g*dt; y = Math.max(0, y - v*dt); }
          setReadout('v=' + v.toFixed(2) + ' m/s  y=' + y.toFixed(2) + ' m');
        }
      },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        var w = canvas.width, h = canvas.height;
        ctx.clearRect(0,0,w,h);
        ctx.fillStyle = '#0f172a'; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#38bdf8';
        if (recipe === 'pendulum'){
          var cx = w/2, cy = 16, Lpx = Math.min(h-36, 80 + num('param-L',1)*40);
          var x = cx + Lpx * Math.sin(theta), yb = cy + Lpx * Math.cos(theta);
          ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(x,yb); ctx.stroke();
          ctx.beginPath(); ctx.arc(x,yb,10,0,Math.PI*2); ctx.fill();
        } else {
          var y0 = Math.max(1, num('param-y0',20));
          var pad = 20;
          var groundY = h - pad;
          var topY = pad + 10;
          var span = Math.max(40, groundY - topY);
          var py = topY + span * (1 - y / y0);
          ctx.strokeStyle = '#94a3b8';
          ctx.beginPath(); ctx.moveTo(pad, topY); ctx.lineTo(pad, groundY); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(w * 0.22, groundY); ctx.lineTo(w * 0.82, groundY); ctx.stroke();
          ctx.fillStyle = '#e2e8f0';
          ctx.font = '12px sans-serif';
          ctx.fillText('y0', pad + 6, topY);
          ctx.fillText('地面', Math.max(pad + 6, w * 0.55), groundY - 6);
          ctx.fillStyle = '#38bdf8';
          ctx.fillRect(w / 2 - 8, py, 16, 16);
          ctx.fillStyle = '#f8fafc';
          ctx.fillText('m', w / 2 - 5, Math.max(topY, py - 6));
          var labelY = Math.min(groundY - 22, Math.max(topY + 14, py + 16));
          ctx.fillText('y=' + y.toFixed(1) + ' m', pad + 6, labelY);
          ctx.fillText('v=' + v.toFixed(1) + ' m/s', pad + 6, Math.min(groundY - 8, labelY + 14));
          if (v > 0.05){
            var tip = Math.min(groundY - 4, py + 16 + Math.min(36, v * 2));
            ctx.strokeStyle = '#f97316';
            ctx.beginPath(); ctx.moveTo(w / 2, py + 16); ctx.lineTo(w / 2, tip); ctx.stroke();
          }
        }
      }
    };
  })();
"""


_WAVE_JS = r"""
  window.WorkFamily = (function(){
    var time = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function phaseOffset(){
      return num('param-phi', 1.2);
    }
    return {
      applyParams: function(){
        var A1 = num('param-A1',0.6), A2 = num('param-A2',0.6);
        var lam = num('param-lambda',80), phi = phaseOffset();
        setReadout('λ=' + lam.toFixed(0) + '  φ=' + phi.toFixed(2) + ' rad  A1+A2=' + (A1+A2).toFixed(2) + ' (axis uses 2A)');
      },
      onStart: function(){ if (time === 0) time = 0.2; },
      reset: function(){ time = 0.2; this.applyParams(); },
      step: function(dt){ time += dt * 2.2; this.applyParams(); },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        var w = canvas.width, h = canvas.height;
        var A1 = num('param-A1',0.6), A2 = num('param-A2',0.6);
        var lam = Math.max(8, num('param-lambda',80));
        var phi = phaseOffset();
        var A = Math.max(0.2, A1+A2);
        var mid = h/2, amp = (h/2 - 20) / (2*Math.max(A, 0.4));
        var k = 2*Math.PI/lam;
        function sample(x){
          return A1*Math.sin(k*x - time) + A2*Math.sin(k*x - time + phi);
        }
        ctx.clearRect(0,0,w,h);
        ctx.fillStyle = '#0f172a'; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = '#64748b';
        ctx.beginPath(); ctx.moveTo(0,mid); ctx.lineTo(w,mid); ctx.stroke();
        function strokeWave(color, width, fn){
          ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath();
          for (var x=0;x<w;x++){
            var py = mid - fn(x) * amp;
            if (x===0) ctx.moveTo(x,py); else ctx.lineTo(x,py);
          }
          ctx.stroke();
        }
        strokeWave('#7dd3fc', 1.25, function(x){ return A1*Math.sin(k*x - time); });
        strokeWave('#fdba74', 1.25, function(x){ return A2*Math.sin(k*x - time + phi); });
        strokeWave('#e2e8f0', 2.2, sample);
        ctx.fillStyle = '#e2e8f0';
        ctx.font = '12px sans-serif';
        ctx.fillText('φ=' + phi.toFixed(2), 16, 18);
        ctx.fillText('λ=' + lam.toFixed(0), 16, 34);
      }
    };
  })();
"""


_RAY_OPTICS_JS = r"""
  window.WorkFamily = (function(){
    var angleDeg = 30;
    var angleRad = 30 * Math.PI / 180;
    var pulse = 0;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function syncAngle(){
      angleDeg = Math.max(10, Math.min(70, num('param-theta', 30)));
      angleRad = angleDeg * Math.PI / 180;
      setReadout('θᵢ=' + angleDeg.toFixed(0) + '°  θᵣ=' + angleDeg.toFixed(0) + '°  反射定律 θᵢ=θᵣ');
      return angleRad;
    }
    function arrow(ctx, x1, y1, x2, y2){
      ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
      var ang = Math.atan2(y2-y1, x2-x1);
      ctx.beginPath();
      ctx.moveTo(x2,y2);
      ctx.lineTo(x2 - 10*Math.cos(ang-0.4), y2 - 10*Math.sin(ang-0.4));
      ctx.lineTo(x2 - 10*Math.cos(ang+0.4), y2 - 10*Math.sin(ang+0.4));
      ctx.closePath(); ctx.fill();
    }
    return {
      applyParams: function(){ syncAngle(); },
      onStart: function(){ if (pulse === 0) pulse = 0.05; },
      reset: function(){ pulse = 0; syncAngle(); },
      step: function(dt){ pulse = (pulse + dt * 0.55) % 2; },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        syncAngle();
        var w = canvas.width, h = canvas.height;
        var pad = 22;
        var cy = h / 2;
        var length = Math.min((w - 2 * pad) * 0.42, h / 2 - pad - 8);
        var mx = Math.min(w - pad - 20, pad + 24 + length);
        var ix = mx - length * Math.cos(angleRad);
        var iy = cy - length * Math.sin(angleRad);
        var rx = mx - length * Math.cos(angleRad);
        var ry = cy + length * Math.sin(angleRad);
        var nx = mx - Math.min(length * 0.72, mx - pad);
        ctx.clearRect(0,0,w,h);
        ctx.fillStyle = '#0f172a'; ctx.fillRect(0,0,w,h);
        ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(mx, pad); ctx.lineTo(mx, h-pad); ctx.stroke();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = '#94a3b8'; ctx.setLineDash([5,4]);
        ctx.beginPath(); ctx.moveTo(mx, cy); ctx.lineTo(nx, cy); ctx.stroke();
        ctx.setLineDash([]);
        ctx.strokeStyle = '#38bdf8'; ctx.fillStyle = '#38bdf8';
        arrow(ctx, ix, iy, mx, cy);
        ctx.strokeStyle = '#f97316'; ctx.fillStyle = '#f97316';
        arrow(ctx, mx, cy, rx, ry);
        ctx.strokeStyle = '#e2e8f0';
        ctx.beginPath(); ctx.arc(mx, cy, Math.min(36, length*0.28), Math.PI-angleRad, Math.PI, false); ctx.stroke();
        ctx.beginPath(); ctx.arc(mx, cy, Math.min(36, length*0.28), Math.PI, Math.PI+angleRad, false); ctx.stroke();
        if (pulse > 0){
          var u = pulse <= 1 ? pulse : pulse - 1;
          var px = pulse <= 1 ? ix + (mx-ix)*u : mx + (rx-mx)*u;
          var py = pulse <= 1 ? iy + (cy-iy)*u : cy + (ry-cy)*u;
          ctx.fillStyle = '#facc15';
          ctx.beginPath(); ctx.arc(px, py, 5, 0, Math.PI*2); ctx.fill();
        }
        ctx.fillStyle = '#e2e8f0';
        ctx.font = '12px sans-serif';
        ctx.fillText('镜', Math.max(pad, mx - 20), h - pad);
        ctx.fillText('法线', Math.max(pad, (nx + mx) / 2 - 12), Math.max(pad + 12, cy - 12));
        ctx.fillText('入射角 '+angleDeg.toFixed(0)+'°', Math.max(pad, ix + 8), Math.max(pad + 12, iy + 14));
        ctx.fillText('反射角 '+angleDeg.toFixed(0)+'°', Math.max(pad, rx + 8), Math.min(h - pad, ry - 6));
      }
    };
  })();
"""


_COMPARTMENT_JS = r"""
  window.WorkFamily = (function(){
    var recipe = "__RECIPE__";
    var vin = 0.45, vout = 0.45, x = 1.2, y = 0.6;
    var hist = [];
    var maxHist = 160;
    var delta = 0.4;
    function num(id, fallback){
      var el = document.getElementById(id);
      return el ? +el.value : fallback;
    }
    function setReadout(text){
      var out = document.getElementById('work-readout');
      if (out) out.textContent = text;
    }
    function record(){
      hist.push([x, y]);
      if (hist.length > maxHist) hist.shift();
    }
    return {
      applyParams: function(){
        if (recipe === 'osmosis'){
          setReadout('Δc=' + (num('param-cin',0.8)-num('param-cout',0.2)).toFixed(2));
        } else {
          setReadout('猎物x=' + x.toFixed(2) + '  捕食者y=' + y.toFixed(2) + '  δ=' + delta.toFixed(2));
        }
      },
      onStart: function(){
        if (recipe === 'osmosis'){ vin = 0.35; vout = 0.55; }
        if (recipe === 'population'){
          if (x < 0.2){ x = 1.2; y = 0.6; }
          if (!hist.length) record();
        }
      },
      reset: function(){
        if (recipe === 'osmosis'){ vin = 0.45; vout = 0.45; }
        else { x = 1.2; y = 0.6; hist = []; record(); }
        this.applyParams();
      },
      step: function(dt){
        if (recipe === 'osmosis'){
          var cin = num('param-cin',0.8), cout = num('param-cout',0.2), k = num('param-k',0.15);
          var J = k * (cin - cout);
          vin = Math.min(0.85, Math.max(0.15, vin + J*dt*0.15));
          vout = Math.min(0.85, Math.max(0.15, vout - J*dt*0.15));
          setReadout('J=' + J.toFixed(3) + '  Vin=' + vin.toFixed(2));
        } else {
          var a = num('param-alpha',0.8), b = num('param-beta',0.5), g = num('param-gamma',0.4);
          var dx = x * (a - b*y);
          var dy = y * (delta*x - g);
          x = Math.max(0.02, x + dx*dt);
          y = Math.max(0.02, y + dy*dt);
          record();
          setReadout('猎物x=' + x.toFixed(2) + '  捕食者y=' + y.toFixed(2));
        }
      },
      draw: function(ctx, canvas){
        if (!ctx || !canvas) return;
        var w = canvas.width, h = canvas.height;
        ctx.clearRect(0,0,w,h);
        ctx.fillStyle = '#0f172a'; ctx.fillRect(0,0,w,h);
        if (recipe === 'osmosis'){
          ctx.fillStyle = '#0ea5e9'; ctx.fillRect(20, h*(1-vin), w*0.35, h*vin);
          ctx.fillStyle = '#14b8a6'; ctx.fillRect(w*0.55, h*(1-vout), w*0.35, h*vout);
          ctx.strokeStyle = '#f8fafc'; ctx.strokeRect(w*0.47, 10, 8, h-20);
        } else {
          var pad = 22, mid = Math.floor(w * 0.58);
          ctx.font = '12px sans-serif';
          ctx.fillStyle = '#22c55e'; ctx.fillRect(pad, 6, 8, 8);
          ctx.fillStyle = '#e2e8f0'; ctx.fillText('猎物 x', pad + 12, 14);
          ctx.fillStyle = '#f97316'; ctx.fillRect(pad + 80, 6, 8, 8);
          ctx.fillStyle = '#e2e8f0'; ctx.fillText('捕食者 y', pad + 92, 14);
          ctx.strokeStyle = '#334155';
          ctx.beginPath(); ctx.moveTo(pad, h-pad); ctx.lineTo(mid-10, h-pad); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(mid, h-pad); ctx.lineTo(w-12, h-pad); ctx.stroke();
          if (hist.length){
            var maxX = 0.2, maxY = 0.2, i, px, py;
            for (i=0;i<hist.length;i++){ if (hist[i][0]>maxX) maxX=hist[i][0]; if (hist[i][1]>maxY) maxY=hist[i][1]; }
            maxX *= 1.2; maxY *= 1.2;
            ctx.strokeStyle = '#22c55e'; ctx.beginPath();
            for (i=0;i<hist.length;i++){
              px = pad + (mid-10-pad) * (i / Math.max(1, maxHist-1));
              py = h-pad - (h-2*pad) * (hist[i][0]/maxX);
              if (i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py);
            }
            ctx.stroke();
            ctx.strokeStyle = '#f97316'; ctx.beginPath();
            for (i=0;i<hist.length;i++){
              px = pad + (mid-10-pad) * (i / Math.max(1, maxHist-1));
              py = h-pad - (h-2*pad) * (hist[i][1]/maxY);
              if (i===0) ctx.moveTo(px,py); else ctx.lineTo(px,py);
            }
            ctx.stroke();
            ctx.fillStyle = '#38bdf8';
            for (i=0;i<hist.length;i++){
              px = mid + (w-12-mid) * (hist[i][0]/maxX);
              py = h-pad - (h-2*pad) * (hist[i][1]/maxY);
              ctx.fillRect(px, py, 2, 2);
            }
          }
        }
      }
    };
  })();
"""
