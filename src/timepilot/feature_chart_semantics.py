"""Source-pinned, entity-safe and extremum-preserving time series aggregation.

The caller has verified asset bytes and confirmed the field contract. All SQL
identifiers are quoted by the caller, every filter value is bound as a parameter.
An overview scans the *whole selected entity/time range*: it must never silently
return the first N records as if they represented the entire history.
"""
from __future__ import annotations

from timepilot.native_bi import quote_identifier, safe_json


def validate_series_args(*, entity_value=None, start=None, end=None, resolution=120):
    n=int(resolution)
    if not 12 <= n <= 300:
        raise ValueError('时间图分辨率须在 12—300 之间')
    for name,value in [('设备值',entity_value),('起始时间',start),('结束时间',end)]:
        if value is not None and len(str(value))>160:
            raise ValueError(name+'过长')
    if start is not None and end is not None and str(start)>str(end):
        # ISO 8601 timestamps compare chronologically. Numeric order keys are
        # checked by the database too, so do not impose this string order there.
        if '-' in str(start) and '-' in str(end):
            raise ValueError('结束时间不能早于起始时间')
    return n


def _dicts(cursor):
    names=[col[0] for col in cursor.description]
    return [dict(zip(names,safe_json(row))) for row in cursor.fetchall()]


def series_aggregate(con, table, *, order_column, value_expression,
                     original_expression='NULL', entity_column=None,
                     entity_field_id=None, entity_value=None,
                     start=None, end=None, resolution=120):
    """Return overview statistics and min/max/mean points, no cross-entity lines.

    `table`, `order_column` and expressions must originate from a trusted
    compiler and confirmed source contract (not from incoming query text).
    """
    resolution=validate_series_args(entity_value=entity_value,start=start,
                                    end=end,resolution=resolution)
    group_expr=quote_identifier(entity_column) if entity_column else None
    order_expr=quote_identifier(order_column)
    options=[];selected=entity_value;selector='none'
    if group_expr:
        options=_dicts(con.execute(f'''SELECT CAST({group_expr} AS VARCHAR) AS value,
            COUNT(*) AS row_count FROM {table} WHERE {group_expr} IS NOT NULL
            GROUP BY {group_expr} ORDER BY row_count DESC, value LIMIT 30'''))
        if selected is None:
            if options:
                selected=options[0]['value'];selector='auto_largest_group'
            else:
                selected=None;selector='no_groups'
        else:
            selector='explicit'
            if str(selected) not in {str(row['value']) for row in options}:
                # A valid group may lie beyond the 30-option preview. Check
                # against the full dataset to avoid silently selecting another.
                count=con.execute(f'''SELECT COUNT(*) FROM {table}
                    WHERE CAST({group_expr} AS VARCHAR)=?''',[str(selected)]).fetchone()[0]
                if not count:raise ValueError('所选设备不在当前数据中')
    where=[f'{order_expr} IS NOT NULL'];params=[]
    if group_expr:
        if selected is None:where.append('FALSE')
        else:
            where.append(f'CAST({group_expr} AS VARCHAR)=?');params.append(str(selected))
    if start is not None:
        where.append(f'{order_expr} >= ?');params.append(str(start))
    if end is not None:
        where.append(f'{order_expr} <= ?');params.append(str(end))
    clause=' AND '.join(where)
    # Sort once and bucket the *entire* selected history. Min/max values are
    # retained in every bucket so late spikes cannot disappear by sampling.
    sql=f'''WITH filtered AS (
        SELECT {order_expr} AS x, __tp_rowid AS row_id,
               {value_expression} AS value,
               {original_expression} AS original
          FROM {table} WHERE {clause}
    ), numbered AS (
        SELECT x, row_id, value, original,
               ROW_NUMBER() OVER (ORDER BY x,row_id) AS seq,
               COUNT(*) OVER () AS population FROM filtered
    ), bucketed AS (
        SELECT *, CAST(FLOOR((seq-1)*? / population) AS INTEGER) AS bucket
          FROM numbered
    ) SELECT bucket, MIN(x) AS x_start, MAX(x) AS x_end,
             MIN(value) AS min_value, MAX(value) AS max_value,
             AVG(value) AS value, AVG(original) AS original,
             COUNT(*) AS row_count, COUNT(value) AS finite_count
        FROM bucketed GROUP BY bucket ORDER BY bucket'''
    points=_dicts(con.execute(sql,[*params,resolution]))
    count=sum(int(p['row_count']) for p in points)
    finite=sum(int(p['finite_count']) for p in points)
    # Do not report the data as a raw 120-record preview: these are aggregate
    # buckets spanning all rows in the selected entity/time interval.
    output=[{'x':str(p['x_start']), 'x_end':str(p['x_end']),
             'value':p['value'],'original':p['original'],
             'min':p['min_value'],'max':p['max_value'],
             'count':int(p['row_count']),'finite_count':int(p['finite_count'])}
            for p in points]
    return {'points':output,'population_count':count,'finite_count':finite,
            'returned_count':len(output),'sampling_method':'ordered_equal_count_buckets_min_max_mean',
            'scope':'selected_entity_and_time_range_full_aggregate',
            'partial':False,'entity_options':options,'entity_field_id':entity_field_id,
            'entity_value':str(selected) if selected is not None else None,
            'entity_selection':selector,'grain':'one_confirmed_entity_per_trajectory' if group_expr else 'unpartitioned_sequence',
            'time_range':{'start':output[0]['x'] if output else None,
                          'end':output[-1]['x_end'] if output else None},
            'resolution':resolution}
