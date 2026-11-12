"""Fixed SQL templates for FeatureSpec v2; accepts zero user-provided SQL."""
from __future__ import annotations
from timepilot.feature_contracts import DataContract, FeatureSpecV2, digest
from timepilot.feature_dag import ordered_features
from timepilot.native_bi import quote_identifier
import re

NUMERIC = re.compile(r'^(TINYINT|SMALLINT|INTEGER|INT\d*|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|REAL|DECIMAL|NUMERIC)\b',re.I)


class FeatureCompiler:
    def __init__(self,contract:DataContract,spec:FeatureSpecV2):
        self.contract=contract
        self.spec=spec
        self.order=ordered_features(spec,contract)
        self.fields={f.field_id:f for f in contract.fields}
        self.names={f.field_id:f.source_name for f in contract.fields}
        self.types={f.field_id:f.physical_type for f in contract.fields}
        self._validate_types()

    def _validate_types(self):
        for feat in self.order:
            for fid in feat.input_field_ids:
                typ=self.types.get(fid,'DOUBLE')
                if feat.operator_id=='event.since':
                    field=self.fields.get(fid)
                    if (field is None or str(typ).upper()!='BOOLEAN'
                        or field.semantic_type not in ('boolean','event')
                        or field.confirmation!='confirmed'):
                        raise ValueError('维护重置标记必须为经确认的 BOOLEAN 事件字段')
                    if not self.contract.event_time_field_id or not self.contract.timezone:
                        raise ValueError('维护事件特征必须先确认真实事件时间和时区')
                elif not NUMERIC.match(str(typ)):
                    raise ValueError(f'数值算子不能使用非数值字段 {fid}: {typ}')
                elif feat.operator_id in ('expression.safe','relative.change',
                        'rolling.median','rolling.p90','rolling.range','rolling.cv',
                        'rolling.zscore','rolling.sum','transform.log1p',
                        'transform.sqrt','transform.abs') and fid in self.fields:
                    from timepilot.feature_operator_registry import compatible_fields
                    if fid not in compatible_fields(self.contract,'numeric'):
                        raise ValueError('高级特征不能将时间/编号/标签当作普通数值输入')
            self.names[feat.feature_id]=feat.output_name
            self.types[feat.feature_id]='DOUBLE'
        task=self.spec.task
        if task and task.mode=='supervised':
            from timepilot.feature_safety import ensure_supervised_contract
            ensure_supervised_contract(self.contract,task)
            for fid in (self.contract.event_time_field_id,self.contract.available_at_field_id,
                        task.prediction_time_field_id,task.label_available_at_field_id):
                typ=self.types.get(fid,'')
                if not any(x in str(typ).upper() for x in ('TIMESTAMP','DATE')):
                    raise ValueError('正式预测任务必须先将时间字段转换为真实日期时间类型')
            if not self.contract.entity_field_ids or self.contract.state!='confirmed':
                raise ValueError('正式预测任务需要确认实体和时间契约')
            sensitive={task.label_field_id,task.label_available_at_field_id,task.label_time_field_id}
            for f in self.order:
                if set(f.input_field_ids)&sensitive:
                    raise ValueError('目标标签或结果可用时间不能作为当前预测输入')
                if any(fid not in self.fields for fid in f.input_field_ids):
                    raise ValueError('监督特征暂不允许引用上游派生列，须先验证逐层可用时间')

    def _window(self,feature):
        params=feature.parameters
        order_id=params.get('order_field_id')
        if order_id is None:
            order_id=self.contract.event_time_field_id if self.spec.task and self.spec.task.mode=='supervised' else None
        if feature.operator_id.startswith(('rolling.duration.','event.since')):
            if order_id!=self.contract.event_time_field_id or not self.contract.timezone:
                raise ValueError('时长窗口只能绑定已确认的事件时间与时区')
            f=self.fields.get(order_id)
            if f is None or f.semantic_type!='datetime' or f.confirmation!='confirmed':
                raise ValueError('时长窗口时间字段未经确认')
        if self.spec.task and self.spec.task.mode=='supervised' and order_id!=self.contract.event_time_field_id:
            raise ValueError('正式预测时序计算只能按已确认事件时间排序')
        if order_id is None:
            order='"__tp_rowid"'
        else:
            if order_id not in self.fields:raise ValueError('时间/排序字段 ID 不存在')
            order=quote_identifier(self.names[order_id]) + ('' if feature.operator_id.startswith('rolling.duration.') else ', "__tp_rowid"')
        partitions=params.get('partition_field_ids')
        if partitions is None:partitions=self.contract.entity_field_ids
        if not isinstance(partitions,list) or any(p not in self.fields for p in partitions):
            raise ValueError('窗口分组字段无效')
        if self.spec.task and self.spec.task.mode=='supervised' and set(partitions)!=set(self.contract.entity_field_ids):
            raise ValueError('正式预测窗口必须仅按已确认实体键分组，不能按标签或未来信息分组')
        if partitions:
            order='PARTITION BY '+', '.join(quote_identifier(self.names[p]) for p in partitions)+' ORDER BY '+order
        else:order='ORDER BY '+order
        return 'OVER ('+order

    def expression(self,feature):
        ids=feature.input_field_ids
        source='TRY_CAST('+quote_identifier(self.names[ids[0]])+' AS DOUBLE)'
        op=feature.operator_id
        p=feature.parameters
        if op=='event.since':
            event_field=quote_identifier(self.names[ids[0]])
            event_time=quote_identifier(self.names[self.contract.event_time_field_id])
            over=self._window(feature)
            last_event=f'MAX(CASE WHEN {event_field} IS TRUE THEN {event_time} END) {over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'
            return f'DATE_DIFF(\'second\', {last_event}, {event_time})'
        if op=='expression.safe':
            from timepilot.feature_expression import compile_formula
            sources=[quote_identifier(self.names[fid]) for fid in ids]
            units=[self.fields[fid].unit if fid in self.fields else None for fid in ids]
            return compile_formula(p['expression'],sources,units)
        if op=='transform.abs':return f'ABS({source})'
        if op=='transform.sqrt':return f'(CASE WHEN {source} >= 0 THEN SQRT({source}) ELSE NULL END)'
        if op=='transform.log1p':return f'(CASE WHEN {source} > -1 THEN LN(1 + {source}) ELSE NULL END)'
        if op=='arithmetic.binary':
            sign=p.get('operator','+')
            first_native=quote_identifier(self.names[ids[0]])
            second_native=quote_identifier(self.names[ids[1]])
            # Preserve DECIMAL / integer arithmetic when the operation permits.
            # Division uses DOUBLE to avoid unintended integer truncation.
            if sign=='/':
                second='TRY_CAST('+second_native+' AS DOUBLE)'
                return f'({source} / NULLIF({second},0))'
            first_unit=self.fields.get(ids[0]).unit if ids[0] in self.fields else None
            second_unit=self.fields.get(ids[1]).unit if ids[1] in self.fields else None
            if sign in ('+','-') and first_unit and second_unit and first_unit!=second_unit:
                raise ValueError('不同单位的数值列不能直接相加减，请先确认换算')
            return f'({first_native} {sign} {second_native})'
        win=self._window(feature)
        if op in ('lag.rows','difference.rows','relative.change'):
            lag=int(p.get('lag',1))
            earlier=f'LAG({source},{lag}) {win})'
            if op=='lag.rows':return earlier
            if op=='relative.change':return f'(({source} - {earlier}) / NULLIF(ABS({earlier}),0))'
            return f'({source} - {earlier})'
        if op.startswith('rolling.duration.'):
            frame=f"{win} RANGE BETWEEN INTERVAL '{int(p['window_seconds'])} seconds' PRECEDING AND CURRENT ROW)"
            min_periods=int(p.get('min_periods',1))
        else:
            window=int(p.get('window',5))
            min_periods=int(p.get('min_periods',window))
            frame=f'{win} ROWS BETWEEN {window-1} PRECEDING AND CURRENT ROW)'
        agg={'rolling.mean':'AVG','rolling.std':'STDDEV_SAMP','rolling.min':'MIN',
             'rolling.sum':'SUM','rolling.median':'MEDIAN','rolling.p90':'QUANTILE_CONT',
             'rolling.max':'MAX','rolling.duration.mean':'AVG',
             'rolling.duration.min':'MIN','rolling.duration.max':'MAX'}.get(op)
        if op in ('rolling.range','rolling.cv','rolling.zscore'):
            count=f'COUNT({source}) {frame}'
            if op=='rolling.range':
                body=f'(MAX({source}) {frame} - MIN({source}) {frame})'
            elif op=='rolling.cv':
                body=f'(STDDEV_SAMP({source}) {frame} / NULLIF(ABS(AVG({source}) {frame}),0))'
            else:
                body=f'(({source} - AVG({source}) {frame}) / NULLIF(STDDEV_SAMP({source}) {frame},0))'
            # Single valid observation cannot define sample variance.
            min_valid=max(min_periods,2) if op in ('rolling.cv','rolling.zscore') else min_periods
            return f'(CASE WHEN {count} >= {min_valid} THEN {body} ELSE NULL END)'
        if not agg:raise ValueError('未注册的特征编译器：'+op)
        counted=f'COUNT({source}) {frame}'
        value=f'{agg}({source}, 0.9) {frame}' if op=='rolling.p90' else f'{agg}({source}) {frame}'
        return f'(CASE WHEN {counted} >= {min_periods} THEN {value} ELSE NULL END)'

    def compile_views(self, con, initial='__tp_effective'):
        table=initial
        for i,feature in enumerate(self.order):
            expr=self.expression(feature)
            next_table=f'__tp_fl2_stage_{i}'
            con.execute(f'CREATE TEMP VIEW {next_table} AS SELECT *, {expr} AS {quote_identifier(feature.output_name)} FROM {table}')
            table=next_table
        return table

    def validate_point_in_time(self,con,source='__tp_effective'):
        task=self.spec.task
        if not task or task.mode!='supervised':return {'status':'exploration','invalid_rows':0}
        event=quote_identifier(self.names[self.contract.event_time_field_id])
        available=quote_identifier(self.names[self.contract.available_at_field_id])
        prediction=quote_identifier(self.names[task.prediction_time_field_id])
        # Intentionally strict: late arrivals are rejected, not silently erased.
        bad=con.execute(f'''SELECT COUNT(*) FROM {source} WHERE
          {event} IS NULL OR {available} IS NULL OR {prediction} IS NULL
          OR {event} > {prediction} OR {available} > {prediction}''').fetchone()[0]
        if bad:raise ValueError(f'发现 {bad} 条预测时点前不可用的数据，拒绝作为监督特征执行')
        # A source observation may arrive after its own event time. A rolling
        # feature at decision time T may include it only if available_at<=T.
        # Checking each row alone is insufficient: an earlier reading can
        # arrive later than a subsequent row's decision. Validate every
        # feature window against the CURRENT row's actual decision cutoff.
        for feature in self.order:
            if feature.operator_id in ('arithmetic.binary','expression.safe','transform.abs','transform.log1p','transform.sqrt'):continue
            if any(fid not in self.fields for fid in feature.input_field_ids):
                # Nested features need propagated per-feature availability
                # metadata. Refuse until this capability is implemented.
                raise ValueError('监督窗口暂不支持引用上游派生特征，请先分开验证来源可用时间')
            params=feature.parameters
            over=self._window(feature)
            if feature.operator_id in ('lag.rows','difference.rows','relative.change'):
                lag=int(params.get('lag',1))
                witness=f'LAG({available},{lag}) {over})'
            elif feature.operator_id=='event.since':
                witness=f'MAX({available}) {over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)'
            elif feature.operator_id.startswith('rolling.duration.'):
                seconds=int(params['window_seconds'])
                witness=f"MAX({available}) {over} RANGE BETWEEN INTERVAL '{seconds} seconds' PRECEDING AND CURRENT ROW)"
            else:
                window=int(params.get('window',5))
                witness=f'MAX({available}) {over} ROWS BETWEEN {window-1} PRECEDING AND CURRENT ROW)'
            invalid=con.execute(f'''SELECT COUNT(*) FROM
              (SELECT {prediction} AS decision_at, {witness} AS latest_source_available
               FROM {source}) AS q
              WHERE latest_source_available IS NOT NULL
              AND latest_source_available > decision_at''').fetchone()[0]
            if invalid:
                raise ValueError(f'{feature.feature_id} 的窗口包含 {invalid} 条决策时点尚不可用的历史记录，拒绝执行')
        return {'status':'verified','invalid_rows':0,'scope':'full',
                'task_sha256':digest(task),'contract_revision':self.spec.contract_revision,
                'availability_window_checks':len([f for f in self.order if f.operator_id not in ('arithmetic.binary','expression.safe','transform.abs','transform.log1p','transform.sqrt')])}
