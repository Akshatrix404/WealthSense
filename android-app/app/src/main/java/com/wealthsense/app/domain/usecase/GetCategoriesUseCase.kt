package com.wealthsense.app.domain.usecase

import com.wealthsense.app.data.database.entity.CategoryEntity
import com.wealthsense.app.data.repository.CategoryRepository
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject

class GetCategoriesUseCase @Inject constructor(
    private val categoryRepository: CategoryRepository
) {
    fun execute(): Flow<List<CategoryEntity>> {
        return categoryRepository.getAllCategories()
    }
}