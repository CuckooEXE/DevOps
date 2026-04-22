#include <stdio.h>

#include "mylib.h"

int main(void) {
  printf("%s: 2+3=%d\n", mylib_greeting(), mylib_add(2, 3));
  return 0;
}
