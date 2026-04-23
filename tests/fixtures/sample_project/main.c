#include <stdio.h>

#include "greet_remote.h"
#include "mylib.h"

int main(void) {
  printf("%s: 2+3=%d (%s)\n", mylib_greeting(), mylib_add(2, 3),
         greet_remote());
  return 0;
}
