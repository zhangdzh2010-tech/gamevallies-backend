import { assertCreateResultMeetsQualityGate } from '../src/game/game-quality.policy';

describe('Desktop interaction quality gate', () => {
  const passing = {ran:true,passed:true,contentChanged:true,controlsExercised:1,issues:[],
    viewports:[{width:1366,horizontalOverflow:false},{width:1920,horizontalOverflow:false}]};
  const result = {runtimeProfile:'interactive_experience',qualityScore:0,qualityBreakdown:{}};
  it('accepts completed desktop checks without inventing a gameplay score', () => {
    expect(() => assertCreateResultMeetsQualityGate({...result,runtimeQaReport:passing})).not.toThrow();
  });
  it.each([undefined,{...passing,ran:false},{...passing,contentChanged:false},{...passing,issues:['JS error']},
    {...passing,viewports:[{horizontalOverflow:true}]}])('rejects missing or failed browser checks', runtimeQaReport => {
    expect(() => assertCreateResultMeetsQualityGate({...result,runtimeQaReport})).toThrow('Desktop interaction checks');
  });
  it('retains the game score gate for game profiles', () => {
    expect(() => assertCreateResultMeetsQualityGate({...result,runtimeProfile:'casual_arcade',runtimeQaReport:passing})).toThrow();
  });
});
