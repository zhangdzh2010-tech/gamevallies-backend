import { QUALITY_POLICY } from '../src/game/generated-quality-policy';
import { assertCreateResultMeetsQualityGate, extractOutcomeLabels } from '../src/game/game-quality.policy';

describe('Desktop interaction quality gate', () => {
  const passing = {ran:true,passed:true,contentChanged:true,controlsExercised:1,issues:[],
    viewports:[{width:1366,horizontalOverflow:false},{width:1920,horizontalOverflow:false}]};
  const keys = Object.keys(QUALITY_POLICY.artifact_rubrics.tool.weights);
  const assessment = {policy_version:QUALITY_POLICY.version,artifact_kind:'tool',review_ran:true,passed:true,score:8,critical_issues:[],
    scores:Object.fromEntries(keys.map(key=>[key,8])),evidence:Object.fromEntries(keys.map(key=>[key,'Concrete evidence']))};
  const result = {runtimeProfile:'interactive_experience',qualityScore:8,qualityBreakdown:assessment};
  it('accepts runtime checks and a valid tool score without requiring gameplay', () => {
    expect(() => assertCreateResultMeetsQualityGate({...result,runtimeQaReport:passing})).not.toThrow();
  });
  it('accepts an explicit optional infrastructure soft failure after structured review', () => {
    const deferred = {ran:false,passed:true,softFailed:true,unavailableReason:'TimeoutError',issues:[]};
    expect(() => assertCreateResultMeetsQualityGate({...result,generationTier:'standard',runtimeQaReport:deferred})).not.toThrow();
    expect(() => assertCreateResultMeetsQualityGate({...result,generationTier:'showcase',runtimeQaReport:deferred})).toThrow('Desktop interaction checks');
  });
  it.each([undefined,{...passing,ran:false},{ran:false,passed:true,softFailed:true,issues:[]},{...passing,contentChanged:false},{...passing,issues:['JS error']},
    {...passing,viewports:[{horizontalOverflow:true}]}])('rejects missing or failed browser checks', runtimeQaReport => {
    expect(() => assertCreateResultMeetsQualityGate({...result,runtimeQaReport})).toThrow('Desktop interaction checks');
  });
  it.each([{}, {...assessment,review_ran:false}, {...assessment,score:NaN}, {...assessment,evidence:{}}, {...assessment,critical_issues:['Wrong result']}, {...assessment,scores:{...assessment.scores,functional_correctness:3}}])('rejects missing or contradictory type assessment',qualityBreakdown=>{
    expect(()=>assertCreateResultMeetsQualityGate({...result,runtimeQaReport:passing,qualityBreakdown})).toThrow('Artifact-specific');
  });
  it('retains the game score gate for game profiles', () => {
    expect(() => assertCreateResultMeetsQualityGate({...result,runtimeProfile:'casual_arcade',qualityScore:0,runtimeQaReport:passing})).toThrow();
  });
  it('derives seedWorthy for unlabeled tool and science successes', () => {
    expect(extractOutcomeLabels(assessment)).toEqual({
      seedWorthy: true,
      seedWorthyReason: 'structured_review_passed',
      pipelineSuccess: true,
    });
    expect(extractOutcomeLabels({review_fun_score: 7.2})).toEqual({
      seedWorthy: null,
      seedWorthyReason: null,
      pipelineSuccess: null,
    });
    expect(QUALITY_POLICY.tiers.standard.fun_score).toBe(6.8);
  });
});
