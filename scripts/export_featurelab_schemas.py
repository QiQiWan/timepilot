"""Export the authoritative FeatureLab contracts from Pydantic without web access."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.feature_contracts import (FieldContract,DataContract,TaskSpecV2,
    FeatureSpecV2,FeatureDefinition,FeatureRunRecord,EvaluationProtocol,PublishedFeatureManifest)

def main():
    folder=ROOT/'contracts'/'featurelab';folder.mkdir(parents=True,exist_ok=True)
    for name,model in [('field_contract',FieldContract),('data_contract',DataContract),
                       ('task_spec',TaskSpecV2),('feature_spec_v2',FeatureSpecV2),
                       ('feature_definition',FeatureDefinition),('feature_run',FeatureRunRecord),
                       ('evaluation_protocol',EvaluationProtocol),('published_feature_manifest',PublishedFeatureManifest)]:
        (folder/f'{name}.schema.json').write_text(
           json.dumps(model.model_json_schema(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Exported 8 authoritative schemas to {folder}')

if __name__=='__main__':main()
