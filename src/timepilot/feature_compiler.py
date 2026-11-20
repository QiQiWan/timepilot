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
            if feat.operator_id.startswith('industrial.'):
                from timepilot.feature_industrial import validate_mechanism
                validate_mechanism(self.contract, feat)
            for fid in feat.input_field_ids:
                typ=self.types.get(fid,'DOUBLE')
                if feat.operator_id=='category.is_value':
                    from timepilot.feature_operator_registry import compatible_fields
                    if fid not in compatible_fields(self.contract,'category'):
                        raise ValueError('类别指示值只能引用已确认的普通类别字段')
                elif feat.operator_id=='event.since':
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
            static=task.split_kind in ('kfold','stratified','group')
            if not static:
                for fid in (self.contract.event_time_field_id,self.contract.available_at_field_id,
                            task.prediction_time_field_id,task.label_available_at_field_id):
                    typ=self.types.get(fid,'')
                    if not any(x in str(typ).upper() for x in ('TIMESTAMP','DATE')):
                        raise ValueError('正式时间预测任务必须先将时间字段转换为真实日期时间类型')
                if not self.contract.entity_field_ids:
                    raise ValueError('正式时间预测任务必须确认实体范围')
            forbidden={task.label_field_id,task.label_available_at_field_id,task.label_time_field_id,
                       task.prediction_time_field_id, self.contract.event_time_field_id,
                       self.contract.available_at_field_id}
            row_local={'arithmetic.binary','transform.log1p','transform.abs','transform.sqrt',
                       'category.is_value','expression.safe','unit.convert'}
            from timepilot.feature_operator_registry import compatible_fields
            for f in self.order:
                if static and f.operator_id not in row_local:
                    raise ValueError('静态监督特征仅允许逐行算子，禁止滚动、分组聚合与跨样本计算')
                if any(fid not in self.fields for fid in f.input_field_ids):
                    raise ValueError('监督特征不允许引用上游派生列，需独立核验数据可用性')
                if set(f.input_field_ids)&forbidden:
                    raise ValueError('目标标签、时间或可用时点不能成为预测输入')
                kind='category' if f.operator_id=='category.is_value' else 'numeric'
                if any(fid not in compatible_fields(self.contract,kind) or
                       self.fields[fid].confirmation!='confirmed' or
                       self.fields[fid].classification=='restricted'
                       for fid in f.input_field_ids):
                    raise ValueError('监督特征必须使用已确认的普通输入字段，不得读取目标、编号或敏感字段')

    def _window(self,feature,*,extra_partition_columns=()):
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
            # Legacy explicit manual specs retain rowid behavior for compatibility.
            # v0.32.3 automatic recommendations never use this fallback.
            order='"__tp_rowid"'
        else:
            if order_id not in self.fields:raise ValueError('时间/排序字段 ID 不存在')
            order_field=self.fields[order_id]
            if order_field.confirmation!='confirmed' or not (
                    (order_field.role=='order_index' and NUMERIC.match(order_field.physical_type)) or
                    (order_id==self.contract.event_time_field_id and order_field.semantic_type=='datetime' and self.contract.timezone)):
                raise ValueError('窗口排序键必须是已确认的数值记录顺序或真实事件时间')
            order=quote_identifier(self.names[order_id]) + ('' if feature.operator_id.startswith('rolling.duration.') else ', "__tp_rowid"')
        partitions=params.get('partition_field_ids')
        if partitions is None:partitions=self.contract.entity_field_ids
        if not isinstance(partitions,list) or any(p not in self.fields for p in partitions):
            raise ValueError('窗口分组字段无效')
        if order_id is not None:
            if set(partitions)!=set(self.contract.entity_field_ids):
                raise ValueError('窗口必须按照已确认的实体分组，不允许跨设备混合')
            if any(self.fields[p].confirmation!='confirmed' for p in partitions):
                raise ValueError('窗口实体分组字段尚未完成确认')
        if self.spec.task and self.spec.task.mode=='supervised' and set(partitions)!=set(self.contract.entity_field_ids):
            raise ValueError('正式预测窗口必须仅按已确认实体键分组，不能按标签或未来信息分组')
        partition_keys=[quote_identifier(self.names[p]) for p in partitions]
        partition_keys.extend(quote_identifier(x) for x in extra_partition_columns)
        if partition_keys:
            order='PARTITION BY '+', '.join(partition_keys)+' ORDER BY '+order
        else:order='ORDER BY '+order
        return 'OVER ('+order

    def expression(self,feature,*,mechanism_cycle_column=None,mechanism_seen_column=None):
        ids=feature.input_field_ids
        source='TRY_CAST('+quote_identifier(self.names[ids[0]])+' AS DOUBLE)'
        op=feature.operator_id
        p=feature.parameters
        if op.startswith('industrial.'):
            if not mechanism_cycle_column or not mechanism_seen_column:
                raise ValueError('工业维护特征缺少计算区段，不允许未经重置直接求累计量')
            from timepilot.feature_industrial import compile_reset_expression
            window=self._window(feature,extra_partition_columns=(mechanism_cycle_column,))
            return compile_reset_expression(source,op,window,mechanism_seen_column)
        if op=='category.is_value':
            # Parameter binding is SQL-literal escaped, never model supplied SQL.
            value=str(p['category_value']).replace("'","''")
            col=quote_identifier(self.names[ids[0]])
            return f"(CASE WHEN {col} IS NULL THEN NULL WHEN CAST({col} AS VARCHAR) = '{value}' THEN 1.0 ELSE 0.0 END)"
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

    def _verify_order_keys(self,con,table):
        # Separate data-level check: a contract alone cannot prove uniqueness.
        signatures=set()
        for feat in self.order:
            from timepilot.feature_topology import needs_row_order
            if not needs_row_order(feat.operator_id):continue
            params=feat.parameters
            order_id=params.get('order_field_id')
            if order_id is None:continue  # legacy import-order manual recipe only
            parts=params.get('partition_field_ids',self.contract.entity_field_ids)
            sig=(order_id,tuple(parts))
            if sig in signatures:continue
            signatures.add(sig)
            quoted=quote_identifier(self.names[order_id]);group=[quote_identifier(self.names[f]) for f in parts]
            null_pred=' OR '.join(f'{q} IS NULL' for q in [quoted,*group])
            invalid=con.execute(f'SELECT COUNT(*) FROM {table} WHERE {null_pred}').fetchone()[0]
            if invalid:raise ValueError(f'排序/分组键有 {invalid} 条空值；请修正来源后再生成窗口特征')
            cols=','.join([*group,quoted])
            duplicates=con.execute(f'SELECT 1 FROM {table} GROUP BY {cols} HAVING COUNT(*)>1 LIMIT 1').fetchone()
            if duplicates:raise ValueError('同一设备内存在重复时间/顺序键；请确认去重规则，拒绝按导入顺序打破并列')

    def compile_views(self, con, initial='__tp_effective'):
        self._verify_order_keys(con,initial)
        table=initial
        for i,feature in enumerate(self.order):
            if feature.operator_id.startswith('industrial.'):
                from timepilot.feature_industrial import create_reset_views
                table,cycle_col,seen_col=create_reset_views(con,table,feature,self.contract,i)
                expr=self.expression(feature,mechanism_cycle_column=cycle_col,
                                     mechanism_seen_column=seen_col)
            else:
                expr=self.expression(feature)
            next_table=f'__tp_fl2_stage_{i}'
            con.execute(f'CREATE TEMP VIEW {next_table} AS SELECT *, {expr} AS {quote_identifier(feature.output_name)} FROM {table}')
            table=next_table
        return table

    def validate_point_in_time(self,con,source='__tp_effective'):
        task=self.spec.task
        if not task or task.mode!='supervised':return {'status':'exploration','invalid_rows':0}
        if task.split_kind in ('kfold','stratified','group'):
            # Compiler construction has already forbidden every cross-row or
            # target-derived operation for static tasks. No fabricated times.
            return {'status':'verified','invalid_rows':0,'scope':'full',
                    'validation_kind':'static_row_local','point_in_time_applicable':False,
                    'task_sha256':digest(task),'contract_revision':self.spec.contract_revision,
                    'availability_window_checks':0}
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
            if feature.operator_id in ('arithmetic.binary','expression.safe','transform.abs','transform.log1p','transform.sqrt','category.is_value'):continue
            if any(fid not in self.fields for fid in feature.input_field_ids):
                # Nested features need propagated per-feature availability
                # metadata. Refuse until this capability is implemented.
                raise ValueError('监督窗口暂不支持引用上游派生特征，请先分开验证来源可用时间')
            params=feature.parameters
            over=self._window(feature)
            if feature.operator_id in ('lag.rows','difference.rows','relative.change'):
                lag=int(params.get('lag',1))
                witness=f'LAG({available},{lag}) {over})'
            elif feature.operator_id=='event.since' or feature.operator_id.startswith('industrial.'):
                # Conservative full-history availability bound. Reset/regime
                # signals are stored on source rows, so their arrival matters.
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
                'validation_kind':'temporal_point_in_time','point_in_time_applicable':True,
                'task_sha256':digest(task),'contract_revision':self.spec.contract_revision,
                'availability_window_checks':len([f for f in self.order if f.operator_id not in ('arithmetic.binary','expression.safe','transform.abs','transform.log1p','transform.sqrt')])}
