from onecpase.fitness_anatomy import GROUPS
from onecpase.fitness_library import (EXERCISES, BY_ID, CATEGORIES, LEVELS, IMPACT, FLAGS,
                                      NEEDS_EASIER, select, alternatives)

REGIONS = {g['id'] for g in GROUPS} | {'full'}
MAJOR = ('chest', 'back', 'shoulders', 'core', 'arms', 'glutes', 'thighs', 'calves')


def test_entries_are_well_formed():
    assert len({e['id'] for e in EXERCISES}) == len(EXERCISES)
    for e in EXERCISES:
        assert e['category'] in CATEGORIES and e['level'] in LEVELS and e['impact'] in IMPACT
        assert e['regions'] and set(e['regions']) <= REGIONS
        assert set(e['avoid']) <= set(FLAGS)
        assert e['met'] > 0 and e['goals'] and e['steps'] and e['equipment']
        if e['category'] in NEEDS_EASIER or e['level'] == 'advanced':
            assert e['easier'], e['id']


def test_regions_and_categories_covered():
    for cat in CATEGORIES:
        assert select(category=cat), cat
    for region in MAJOR:
        assert len(select(region=region)) >= 3, region
    for region in REGIONS - {'face'}:
        assert select(region=region), region


def test_requested_endurance_and_step_work_present():
    for key in ('battle_rope', 'kettlebell_swing', 'stair_climb', 'jog_run', 'aerobics_low',
                'box_step_up', 'weighted_step_up', 'step_down_squat', 'drop_squat'):
        assert key in BY_ID, key
    assert BY_ID['step_down_squat']['impact'] != 'high'
    assert not any(e['impact'] == 'high' for e in select(category='functional'))


def test_conditions_and_impact_filter():
    knee = {e['id'] for e in select(exclude=('knee',))}
    assert 'box_jump' not in knee and 'goblet_squat' not in knee and 'plank' in knee
    assert all(e['impact'] != 'high' for e in select(max_impact='low'))
    beginner = select(level='beginner')
    assert beginner and all(e['level'] == 'beginner' for e in beginner)
    assert 'endurance' in BY_ID['battle_rope']['goals']


def test_alternatives_respect_exclusions():
    alts = alternatives('box_jump', exclude=('knee',))
    assert all('knee' not in e['avoid'] and e['category'] == 'plyometric' for e in alts)
