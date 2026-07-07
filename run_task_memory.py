import task_memory
entries = task_memory.retrieve_relevant('what python version does this project use')
print('Entries found:', len(entries))
for e in entries:
    print(' -', e['task'], '->', e['summary'])
