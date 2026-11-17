"""Ordering helpers."""


def quicksort(items):
    if len(items) <= 1:
        return items
    pivot = items[0]
    left = [x for x in items[1:] if x < pivot]
    right = [x for x in items[1:] if x >= pivot]
    return quicksort(left) + [pivot] + quicksort(right)


def insertion_sort(items):
    out = list(items)
    for i in range(1, len(out)):
        j = i
        while j > 0 and out[j] < out[j - 1]:
            out[j], out[j - 1] = out[j - 1], out[j]
            j -= 1
    return out
